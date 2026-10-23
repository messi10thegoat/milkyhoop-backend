"""Nota Kredit -- PENENTU per aksi (NK-RENCANA, 23 Okt 2026; desain disetujui MASTER, KONTRAK-nk-rencana-20261023.md; preseden ND U3d
services/nota_debit_rencana.py). Menggantikan pratinjau tulis-lalu-rollback (_pratinjau_nk_aksi menjalankan inti penulis di savepoint).
SATU definisi blok per aksi, dipakai DUA jalur:
  * inti TULIS (routers/credit_notes: apply/unapply/refund_nota_kredit_core): dipanggil SESUDAH kunci yang sama seperti dulu; blok PERTAMA =
    HTTPException yang SAMA PERSIS (status + detail, termasuk detail dict 409 tolak_nk_bertaut) dengan urutan raise lama;
  * PRATINJAU: transaksi READ ONLY tanpa kunci/sequence/tulis; SEMUA blok dikumpulkan; keadaan sesudah = RUMUS (trigger
    update_credit_note_status + compute_ar_outstanding cabang 2), dibuktikan == tertulis oleh harness uji-nyata-nk-rencana-a.
Tahap A: terapkan (apply) / lepas (unapply) / kembalikan dana (refund). Tahap B (post/void) menyusul.
Pembantu NK (tolak_nk_bertaut, sisa_kredit_nk, resolusi_akun_refund) tinggal di routers/credit_notes -> impor malas (hindari siklus)."""
from decimal import Decimal, ROUND_HALF_UP
from uuid import UUID

from fastapi import HTTPException

from . import teks_galat as tg
from .nota_debit_rencana import Blok, _f, _tampil
from .pihak_helpers import faktur_tenant_untuk_pelanggan, pastikan_cn_muat_faktur

PESAN_NK_TAK_ADA = "Nota kredit tidak ditemukan."


def _nk():
    from ..routers import credit_notes as R
    return R


async def _baca_nk(conn, tid: str, cn_id, bl: Blok):
    """404 = galat (pratinjau pun 404, kontrak lama rute pratinjau)."""
    cn = await conn.fetchrow("SELECT * FROM credit_notes WHERE id = $1 AND tenant_id = $2", cn_id, tid)
    if not cn:
        raise HTTPException(status_code=404, detail=PESAN_NK_TAK_ADA)
    return cn


def _bigint_pg(v) -> int:
    """SUM(numeric) -> BIGINT di trigger update_credit_note_status (pembulatan setengah menjauhi nol)."""
    return int(Decimal(str(v or 0)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def status_nk_sesudah(status_kini: str, total, diterapkan, dikembalikan) -> str:
    """== trigger update_credit_note_status (draf/void tak diubah)."""
    if status_kini in ("draft", "void"):
        return status_kini
    t, d, k = _bigint_pg(total), _bigint_pg(diterapkan), _bigint_pg(dikembalikan)
    if d + k >= t:
        return "applied"
    return "partial" if d + k > 0 else "posted"


async def kredit_piutang_nk(conn, tid: str, cn_id) -> Decimal:
    """Kredit PIUTANG jurnal CREDIT_NOTE efektif NK ini == cabang 2 compute_ar_outstanding (yang berpindah ke faktur saat apply)."""
    v = await conn.fetchval(
        """SELECT COALESCE(SUM(jl.credit), 0) FROM journal_entries je
           JOIN journal_lines jl ON jl.journal_id = je.id
           JOIN chart_of_accounts coa ON coa.id = jl.account_id
           WHERE je.tenant_id = $1 AND je.source_id::uuid = $2 AND je.source_type = 'CREDIT_NOTE'
             AND je.status = 'POSTED' AND je.reversed_by_id IS NULL
             AND coa.account_type = 'RECEIVABLE' AND jl.credit > 0""", tid, cn_id)
    return Decimal(str(v or 0))


# ------------------------------------------------------------------ terapkan (apply)
async def rencana_terapkan_nk(conn, tid: str, cn_id, body, pratinjau: bool = False) -> dict:
    """URUTAN == apply_nota_kredit_core lama: satu faktur · ada (404) · bertaut (tolak_nk_bertaut, 400/409 dict) · sudah diterapkan ·
    terbit ber-jurnal · sudah refund · seluruh nilai · faktur tenant+pelanggan · muat faktur · potongan muat sisa. Tanpa jurnal."""
    R = _nk()
    bl = Blok(pratinjau)
    satu = len(body.applications) == 1
    if not satu:
        bl.galat("CN_APPLY_SATU_FAKTUR", 400, "Nota kredit hanya bisa diterapkan ke satu faktur.")
    cn = await _baca_nk(conn, tid, cn_id, bl)
    await bl.coba("CN_SUDAH_TERKAIT", R.tolak_nk_bertaut(conn, tid, cn))
    if (cn["amount_applied"] or 0) > 0:
        bl.galat("CN_SUDAH_TERKAIT", 400, "Nota kredit ini sudah terkait ke faktur.")
    if cn["status"] != "posted" or cn["journal_id"] is None:
        bl.galat("CN_BELUM_TERBIT", 400, "Hanya nota kredit yang sudah dibukukan yang bisa diterapkan ke faktur.")
    if (cn["amount_refunded"] or 0) > 0:
        bl.galat("CN_SUDAH_DIKEMBALIKAN", 400, "Nota kredit yang dananya sudah dikembalikan tidak bisa diterapkan ke faktur.")
    total = Decimal(str(cn["total_amount"]))
    faktur = None
    if satu:
        app = body.applications[0]
        if Decimal(str(app.amount)) != total:
            bl.galat("CN_APPLY_PENUH", 400, f"Nota kredit harus diterapkan seluruhnya ({tg.rp(total)}) ke satu faktur.")
        faktur = await bl.coba("CN_FAKTUR", faktur_tenant_untuk_pelanggan(conn, tid, app.invoice_id, cn["customer_id"]))
        if faktur:
            await bl.coba("CN_FAKTUR", pastikan_cn_muat_faktur(conn, tid, faktur, total))
            await bl.coba("CN_FAKTUR", R.pastikan_potongan_muat_sisa(conn, tid, faktur, total, cn["reason"]))
    return {"blocks": bl, "cn": cn, "faktur": faktur, "total": total}


# ------------------------------------------------------------------ lepas (unapply)
async def rencana_lepas_nk(conn, tid: str, cn_id, alasan, pratinjau: bool = False) -> dict:
    """URUTAN == unapply_nota_kredit_core lama: alasan wajib · ada (404) · satu penerapan aktif (400 / ganda 409) · periode tanggal penerapan."""
    bl = Blok(pratinjau)
    if not (alasan or "").strip():
        bl.galat("CN_ALASAN_WAJIB", 400, "Alasan pembatalan penerapan wajib diisi.")
    cn = await _baca_nk(conn, tid, cn_id, bl)
    aktif = await conn.fetch(
        """SELECT id, invoice_id, invoice_number, amount_applied, application_date
           FROM credit_note_applications
           WHERE credit_note_id = $1 AND tenant_id = $2 AND status = 'active'""", cn_id, tid)
    app = None
    if not aktif or cn["original_invoice_id"] is None:
        bl.galat("CN_BELUM_DITERAPKAN", 400, "Nota kredit ini belum diterapkan ke faktur mana pun.")
    elif len(aktif) > 1:
        bl.galat("CN_PENERAPAN_GANDA", 409, "Nota kredit ini punya lebih dari satu penerapan aktif; hubungi dukungan.")
    else:
        app = aktif[0]
        r = await conn.fetchrow(
            "SELECT status FROM fiscal_periods WHERE tenant_id = $1 AND start_date <= $2 AND end_date >= $2", tid, app["application_date"])
        if r and r["status"] != "OPEN":
            bl.galat("PERIOD_CLOSED", 400, (
                f"Periode akuntansi penerapan ({app['application_date']:%d-%m-%Y}) sudah {tg.kata_periode(r['status'])}; "
                "penerapan nota kredit tidak bisa dibatalkan."))
    return {"blocks": bl, "cn": cn, "app": app, "alasan": (alasan or "").strip()}


# ------------------------------------------------------------------ kembalikan dana (refund)
def baris_refund_nk(ar_account_id, akun_kredit, jumlah, nomor: str) -> list:
    """[(account_id, debit, credit, memo)] jurnal CREDIT_NOTE_REFUND == penulis lama: baris 1 Dr Piutang, baris 2 Cr akun terpecahkan."""
    return [(ar_account_id, jumlah, 0, f"Refund Piutang - {nomor}"), (akun_kredit, 0, jumlah, f"Pembayaran Refund - {nomor}")]


async def rencana_refund_nk(conn, tid: str, cn_id, body, pratinjau: bool = False) -> dict:
    """URUTAN == refund_nota_kredit_core lama: ada (404) · status terbit/sebagian · bertaut (409 dict) · <= sisa · akun/rekening ·
    bulat bila rekening bank · periode tanggal refund. Status/bertaut gagal -> berhenti (sisa & akun tak bermakna)."""
    R = _nk()
    bl = Blok(pratinjau)
    cn = await _baca_nk(conn, tid, cn_id, bl)
    hasil = {"blocks": bl, "cn": cn, "akun": None, "rekening": None, "cermin": None}
    if cn["status"] not in ("posted", "partial"):
        bl.galat("CN_TAK_BISA_REFUND", 400, tg.tak_bisa_status(
            "cn", R.status_detail_nk(cn["status"], cn["amount_applied"], cn["amount_refunded"]), "dikembalikan dananya"))
        return hasil
    try:
        await R.tolak_nk_bertaut(conn, tid, cn)
    except HTTPException as e:
        bl.galat("CN_TAK_BISA_REFUND", e.status_code, e.detail)
        return hasil
    sisa = R.sisa_kredit_nk(cn["status"], cn["total_amount"], cn["amount_applied"], cn["amount_refunded"], cn["original_invoice_id"])
    if body.amount > sisa:
        bl.galat("CN_REFUND_MELEBIHI_SISA", 400, f"Pengembalian {tg.rp(body.amount)} melebihi sisa nota kredit {tg.rp(sisa)}.")
    akun = await bl.coba("CN_REFUND_AKUN", R.resolusi_akun_refund(conn, tid, body))
    if akun:
        hasil["akun"], hasil["rekening"] = akun
        hasil["cermin"] = hasil["rekening"] or await conn.fetchval(
            "SELECT id FROM bank_accounts WHERE tenant_id = $1 AND coa_id = $2 AND is_active = true", tid, hasil["akun"])
        if hasil["cermin"] and Decimal(str(body.amount)) != Decimal(str(body.amount)).to_integral_value():
            bl.galat("CN_REFUND_BUKAN_BULAT", 422, {"code": "CN_REFUND_BUKAN_BULAT",
                                                    "message": "Jumlah pengembalian lewat rekening bank harus bilangan bulat rupiah."})
    r = await conn.fetchrow(
        "SELECT status FROM fiscal_periods WHERE tenant_id = $1 AND start_date <= $2 AND end_date >= $2", tid, body.refund_date)
    if r and r["status"] != "OPEN":
        bl.galat("PERIOD_CLOSED", 400, tg.periode_tertutup(None, r["status"]))
    return hasil


# ------------------------------------------------------------------ pratinjau (READ ONLY)
def _sesudah_nk(sebelum: dict, cn, d_terapkan, d_kembali, faktur_baru) -> dict:
    R = _nk()
    dipakai = _bigint_pg(Decimal(str(cn["amount_applied"] or 0)) + d_terapkan)
    kembali = _bigint_pg(Decimal(str(cn["amount_refunded"] or 0)) + d_kembali)
    st = status_nk_sesudah(cn["status"], cn["total_amount"], dipakai, kembali)
    sisa = R.sisa_kredit_nk(st, cn["total_amount"], dipakai, kembali, faktur_baru)
    return {**sebelum, "status": st, "status_detail": R.status_detail_nk(st, dipakai, kembali),
            "amount_applied": float(dipakai), "amount_refunded": float(kembali), "remaining": float(sisa)}


async def pratinjau_aksi_nk(conn, ctx: dict, cn_id, aksi: str, body) -> dict:
    """aksi in (apply, unapply, refund); body = ApplyCreditNoteRequest | alasan (str|None) | RefundCreditNoteRequest. Pemanggil membuka
    transaksi READ ONLY. Bentuk respons == pratinjau lama (FE pratinjauAksiNk.ts), journal_number null (preseden ND U3d)."""
    R = _nk()
    tid = ctx["tenant_id"]
    if aksi == "apply":
        r = await rencana_terapkan_nk(conn, tid, cn_id, body, pratinjau=True)
    elif aksi == "unapply":
        r = await rencana_lepas_nk(conn, tid, cn_id, body, pratinjau=True)
    else:
        r = await rencana_refund_nk(conn, tid, cn_id, body, pratinjau=True)
    bl, cn = r["blocks"], r["cn"]
    bisa = not bl.daftar
    sebelum = await R._keadaan_nk(conn, tid, cn_id)
    if aksi == "apply":
        try:
            fid = UUID(str(body.applications[0].invoice_id)) if len(body.applications) == 1 else None
        except ValueError:
            fid = None
    else:
        fid = sebelum.get("original_invoice_id")
    f0 = await R._sisa_faktur_nk(conn, tid, fid)
    sesudah, f1, jurnal, bank = sebelum, f0, [], None
    if bisa and aksi == "apply":
        sesudah = _sesudah_nk(sebelum, cn, r["total"], 0, r["faktur"]["id"])
        f1 = float(Decimal(str(f0)) - await kredit_piutang_nk(conn, tid, cn_id))
    elif bisa and aksi == "unapply":
        sesudah = _sesudah_nk(sebelum, cn, -Decimal(str(r["app"]["amount_applied"])), 0, None)
        f1 = float(Decimal(str(f0)) + await kredit_piutang_nk(conn, tid, cn_id))
    if aksi == "refund":
        if bisa:
            sesudah = _sesudah_nk(sebelum, cn, 0, Decimal(str(body.amount)), cn["original_invoice_id"])
            ar = await R.resolve_account_id(conn, tid, R.AR_ACCOUNT_CODE)
            jurnal = [{"journal_number": None, "source_type": "CREDIT_NOTE_REFUND",
                       "description": f"Refund {cn['credit_note_number']} - {cn['customer_name']}",
                       "journal_date": body.refund_date.isoformat(),
                       "lines": await _tampil(conn, tid, baris_refund_nk(ar, r["akun"], body.amount, cn["credit_note_number"]))}]
        if r["akun"] is not None:
            akun = await conn.fetchrow("SELECT account_code, name FROM chart_of_accounts WHERE id = $1 AND tenant_id = $2", r["akun"], tid)
            s0 = await R._saldo_akun_nk(conn, tid, r["akun"])
            baris_bank = []
            if bisa and r["cermin"]:
                nama = await conn.fetchval("SELECT account_name FROM bank_accounts WHERE id = $1 AND tenant_id = $2", r["cermin"], tid)
                baris_bank = [{"bank_account_id": str(r["cermin"]), "bank_account_name": nama, "transaction_type": "withdrawal",
                               "amount": float(-int(Decimal(str(body.amount))))}]
            bank = {"account_id": str(r["akun"]), "account_code": akun["account_code"] if akun else None,
                    "account_name": akun["name"] if akun else None, "balance_before": s0,
                    "balance_after": float(Decimal(str(s0)) - Decimal(str(body.amount))) if bisa else s0,
                    "bank_transactions": baris_bank}
    for k in (sebelum, sesudah):
        k.pop("original_invoice_id", None)
    if aksi == "unapply":
        payload = {"reason": (body or "").strip() or None}
    else:
        payload = body.model_dump(mode="json")
    return {"credit_note_id": str(cn_id), "action": aksi, "can_proceed": bisa, "blocks": bl.respons(),
            "before": sebelum, "after": sesudah, "journals": jurnal, "bank": bank,
            "invoice": ({"invoice_id": str(fid), "remaining_before": f0, "remaining_after": f1} if fid else None),
            "payload": payload, "preview": True}
