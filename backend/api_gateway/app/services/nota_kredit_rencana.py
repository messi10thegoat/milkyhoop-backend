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
from .nota_debit_rencana import Blok, _tampil
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


# =============================================================================================================================
# TAHAP B (23 Okt 2026): terbitkan (post) / batal (void). Penulis (routers/credit_notes: posting_nota_kredit, void_nota_kredit_core)
# memanggil penentu SESUDAH kunci lama; urutan blok == urutan raise lama. Pratinjau = penentu READ ONLY (kunci=False di
# cn_tertunda / periksa_bisa_dinotakan) + jurnal rencana tanpa nomor.
# =============================================================================================================================
async def _peran(conn, tid: str, role, bl: Blok, pratinjau: bool):
    """resolve_account_id_by_role: TULIS -> AccountRoleUnmappedError merambat apa adanya (handler global 422, perilaku lama);
    PRATINJAU -> blok ACCOUNT_DEFAULT_UNMAPPED 422 dengan pesan handler global."""
    from .role_resolver import AccountRoleUnmappedError
    try:
        return await _nk().resolve_account_id_by_role(conn, tid, role)
    except AccountRoleUnmappedError:
        if not pratinjau:
            raise
        bl.galat("ACCOUNT_DEFAULT_UNMAPPED", 422, "Akun default untuk posting ini belum diatur. Atur di Pengaturan → Akun Default.")
        return None


async def rencana_posting_nk(conn, ctx: dict, cn_id, pratinjau: bool = False, maya: dict = None) -> dict:
    """URUTAN == posting_nota_kredit lama (sesudah kunci CREDIT_NOTE): ada (404) · draf · faktur asal (tenant+pelanggan, muat, potongan,
    bisa dinotakan) · periode tanggal NK · akun piutang + retur/diskon (500) · porsi Dimuka V312 (+ akun REVENUE_DEFERRED) · PPN non-PKP (422) ·
    pecahan piutang/KRD (+ akun Uang Muka Pelanggan) · restock retur (barang belum pernah keluar / melebihi sisa terkirim).
    -> rencana yang DIPAKAI penulis (akun, porsi, porsi_ar, kelebihan) dan pratinjau (+ restock untuk jurnal HPP).
    maya = {"cn": baris NK, "items": baris credit_note_items} (AS-G7b, HANYA pratinjau buat NK): NK yang BELUM ditulis -- id = UUID acak
    yang tak ada di mana pun (penyaring `id <> NK ini` = tak mengecualikan apa pun, sama dengan NK yang belum ada); baris dibaca dari
    maya, bukan dari credit_note_items. Penulis memanggil TANPA maya (perilaku tak berubah)."""
    from .role_resolver import AccountRole
    R = _nk()  # pembantu lewat modul router (satu rujukan dgn penulis; tes menambal R.*)
    tid = ctx["tenant_id"]
    bl = Blok(pratinjau)
    cn = maya["cn"] if maya else await _baca_nk(conn, tid, cn_id, bl)
    cn_id = cn["id"]
    baris_nk = maya["items"] if maya else None
    hasil = {"blocks": bl, "cn": cn, "faktur_asal": None}
    if cn["status"] != "draft":
        bl.galat("CN_NOT_DRAFT", 400, tg.tak_bisa_status(
            "cn", R.status_detail_nk(cn["status"], cn["amount_applied"], cn["amount_refunded"]), "diterbitkan"))
        return hasil
    faktur_asal = None
    if cn["original_invoice_id"]:
        faktur_asal = await bl.coba("CN_FAKTUR_ASAL", R.faktur_tenant_untuk_pelanggan(conn, tid, cn["original_invoice_id"], cn["customer_id"]))
        if faktur_asal:
            await bl.coba("CN_FAKTUR_ASAL", R.pastikan_cn_muat_faktur(conn, tid, faktur_asal, cn["total_amount"], kecuali_cn=cn_id))
            await bl.coba("CN_FAKTUR_ASAL", R.pastikan_potongan_muat_sisa(conn, tid, faktur_asal, cn["total_amount"], cn["reason"]))
            await bl.coba("CN_FAKTUR_ASAL", R.periksa_bisa_dinotakan(
                conn, tid, faktur_asal["id"],
                [{"quantity": r["quantity"], "original_invoice_item_id": r["original_invoice_item_id"]} for r in baris_nk]
                if baris_nk is not None else [dict(r) for r in await conn.fetch(
                    "SELECT quantity, original_invoice_item_id FROM credit_note_items WHERE credit_note_id = $1", cn_id)],
                kecuali_cn=cn_id, kunci=not pratinjau))
    hasil["faktur_asal"] = faktur_asal
    r = await conn.fetchrow("SELECT status FROM fiscal_periods WHERE tenant_id = $1 AND start_date <= $2 AND end_date >= $2",
                            tid, cn["credit_note_date"])
    if r and r["status"] != "OPEN":
        bl.galat("PERIOD_CLOSED", 400, tg.periode_tertutup(None, r["status"]))
    ar = await R.resolve_account_id(conn, tid, R.AR_ACCOUNT_CODE)
    retur = await R.resolve_account_id(conn, tid, R.kode_akun_nk(cn["reason"]))
    if not ar or not retur:
        bl.galat("AKUN_NK_HILANG", 500, "Akun yang diperlukan belum ada di bagan akun.")
    total = cn["total_amount"]
    pajak = cn["tax_amount"] or 0
    subtotal = total - pajak
    porsi, baris_faktur = await R.cn_tertunda.hitung_untuk_nk(
        conn, tid, cn, subtotal, kunci=not pratinjau,
        item_nk=[{"item_id": r["item_id"], "subtotal": r["subtotal"]} for r in baris_nk] if baris_nk is not None else None)
    u = sum(porsi.values(), Decimal("0"))
    dimuka = None
    if u > 0:
        dimuka = await _peran(conn, tid, AccountRole.REVENUE_DEFERRED, bl, pratinjau)
        if not dimuka and not bl.daftar:
            bl.galat("AKUN_DIMUKA_HILANG", 500, "Akun Pendapatan Diterima Dimuka (REVENUE_DEFERRED) tak ditemukan")
    akun_pajak = None
    if pajak > 0:
        akun_pajak = await R.resolve_account_id_by_role_if_pkp(conn, tid, "VAT_OUTPUT")
        if akun_pajak is None:
            bl.galat("CN_PPN_NON_PKP", 422, "Tenant non-PKP tidak dapat post PPN > 0 pada credit note")
    porsi_ar, kelebihan = total, Decimal("0")
    if faktur_asal:
        sisa_ar = Decimal(str(await conn.fetchval(
            "SELECT COALESCE(SUM(outstanding), 0) FROM compute_ar_outstanding($1) WHERE invoice_id = $2", tid, faktur_asal["id"]) or 0))
        porsi_ar = min(Decimal(str(total)), max(sisa_ar, Decimal("0")))
        kelebihan = Decimal(str(total)) - porsi_ar
    akun_krd = await _peran(conn, tid, AccountRole.CUSTOMER_DEPOSIT_LIABILITY, bl, pratinjau) if kelebihan > 0 else None
    hasil.update({"akun": {"ar": ar, "retur": retur, "dimuka": dimuka, "pajak": akun_pajak, "krd": akun_krd}, "porsi": porsi,
                  "baris_faktur": baris_faktur, "u": u, "subtotal": subtotal, "pajak": pajak, "porsi_ar": porsi_ar, "kelebihan": kelebihan})
    # restock (retur): TULIS memeriksa + menulis di loop-nya sendiri (urutan record_inventory_inbound / WAC per baris tak berubah, satu
    # kueri keluar-faktur per baris); PRATINJAU merencanakan dengan aturan yang SAMA -- harness membuktikan blok/jurnal HPP == tertulis.
    hasil["restock"] = await _rencana_restock_nk(conn, ctx, cn, bl, items=baris_nk, semua=maya is not None) if pratinjau else []
    return hasil


async def _rencana_restock_nk(conn, ctx: dict, cn, bl: Blok, items: list = None, semua: bool = False) -> list:
    """== loop restock posting_nota_kredit (#37 HANYA retur; ber-faktur: HPP+gudang dari keluarnya barang; tanpa faktur: WAC + gudang
    pertama). -> [{product_id, nama, qty, unit_cost, line_cost, inv, cogs}] (line_cost > 0 saja ikut jurnal HPP). HANYA pratinjau (tanpa
    kunci CN_RETUR_FAKTUR); penulis memakai loop-nya sendiri dengan aturan + pesan yang sama.
    items = baris NK maya (AS-G7b); semua=True: baris biaya 0 IKUT (penulis tetap mencatat stok masuknya, hanya jurnal HPP yang
    melewatinya) -> pemanggil menyaring line_cost > 0 untuk baris_hpp_nk."""
    from .role_resolver import AccountRole
    R = _nk()
    tid = ctx["tenant_id"]
    if not R._cn_memulihkan_stok(cn["reason"]):
        return []
    out = []
    for item in (items if items is not None else await conn.fetch("SELECT * FROM credit_note_items WHERE credit_note_id = $1", cn["id"])):
        if not item["item_id"]:
            continue
        product = await conn.fetchrow("SELECT id, item_code, nama_produk, track_inventory FROM products WHERE id = $1", item["item_id"])
        if not product or not product["track_inventory"]:
            continue
        qty = Decimal(str(item["quantity"]))
        if cn["original_invoice_id"]:
            asal = await R._keluar_faktur_untuk_retur(conn, tid, cn["original_invoice_id"], product["id"], cn["id"])
            nama = product["nama_produk"] or product["item_code"]
            if asal is None:
                bl.galat("CN_RETUR_BELUM_KELUAR", 400, (
                    f"Barang {nama} belum pernah dikirim lewat faktur {cn['original_invoice_number'] or ''}".rstrip()
                    + ", jadi tidak bisa diretur ke stok. Pilih alasan selain 'Retur barang' bila ini koreksi nilai."))
                continue
            sisa, unit_cost, _wh = asal
            if qty > sisa:
                bl.galat("CN_RETUR_MELEBIHI_TERKIRIM", 400, (
                    f"Retur {nama} {tg.qty(qty)} melebihi yang terkirim dan belum diretur ({tg.qty(max(sisa, Decimal('0')))})."))
                continue
        else:
            avg = await conn.fetchval("SELECT get_weighted_average_cost($1, $2)", tid, product["id"])
            unit_cost = Decimal(str(avg)) if avg else Decimal("0")
            if not await conn.fetchval("SELECT id FROM warehouses WHERE tenant_id = $1 ORDER BY created_at LIMIT 1", tid):
                continue
        line_cost = qty * unit_cost
        if line_cost <= 0:
            if semua:
                out.append({"product_id": product["id"], "nama": product["nama_produk"], "qty": qty, "unit_cost": unit_cost,
                            "line_cost": line_cost, "inv": None, "cogs": None})
            continue
        akun = await conn.fetchrow("SELECT cogs_account_id, inventory_account_id FROM products WHERE id = $1", product["id"])
        cogs = (akun["cogs_account_id"] if akun else None) or await _peran(conn, tid, AccountRole.COGS_SALES, bl, True)
        inv = (akun["inventory_account_id"] if akun else None) or await _peran(conn, tid, AccountRole.INVENTORY_MERCHANDISE, bl, True)
        out.append({"product_id": product["id"], "nama": product["nama_produk"], "qty": qty, "unit_cost": unit_cost, "line_cost": line_cost,
                    "inv": inv, "cogs": cogs})
    return out


def catatan_kelebihan_nk(jumlah) -> dict:
    """Catatan pratinjau NK: kelebihan atas sisa tagihan -> saldo kredit pelanggan (KRD). SATU pembentuk teks ini (pratinjau buat NK;
    pratinjau posting tak membawa daftar catatan, KRD-nya tampil sebagai customer_credit tanpa nomor). Nomor KRD tak disebut (belum
    terbit; penghitungnya dipakai bersama DEP sehingga perkiraan sering meleset), teks ringkas tanpa kurung."""
    return {"code": "CN_EXCESS_TO_CUSTOMER_CREDIT",
            "message": f"{tg.rp(jumlah)} melebihi sisa tagihan, menjadi saldo kredit pelanggan yang bisa dipakai untuk faktur lain "
                       "atau dikembalikan."}


SQL_PERKIRAAN_NOMOR_NK = """SELECT 'CN-' || TO_CHAR(tanggal_bisnis($1), 'YYMM') || '-' || LPAD((COALESCE((
    SELECT last_number FROM credit_note_sequences WHERE tenant_id = $1 AND year_month = TO_CHAR(tanggal_bisnis($1), 'YYYY-MM')), 0) + 1)::text,
    4, '0')"""


async def perkiraan_nomor_nk(conn, tid: str) -> str:
    """Nomor NK yang AKAN diberikan generate_credit_note_number(tid, 'CN') (V302) -- format & penghitung SAMA, tanpa menulis/mengunci.
    PERKIRAAN: penulis paralel bisa mengambilnya dulu (pola NetSuite: nomor final saat simpan)."""
    return await conn.fetchval(SQL_PERKIRAAN_NOMOR_NK, tid)


def baris_posting_nk(cn, akun: dict, u, subtotal, pajak, porsi_ar, kelebihan) -> list:
    """[(account_id, debit, credit, memo)] jurnal CREDIT_NOTE == urutan & memo penulis: Dr Dimuka (porsi) · Dr Retur/Diskon · Dr PPN ·
    Cr Piutang (<= sisa faktur) · Cr Uang Muka Pelanggan (kelebihan -> KRD)."""
    R = _nk()
    no = cn["credit_note_number"]
    b = []
    if u > 0:
        b.append((akun["dimuka"], u, 0, f"Pengurangan Pendapatan Diterima Dimuka - {no}"))
    if subtotal - u > 0:
        b.append((akun["retur"], subtotal - u, 0, f"{'Diskon Penjualan' if cn['reason'] in R.ALASAN_POTONGAN else 'Retur Penjualan'} - {no}"))
    if pajak > 0 and akun.get("pajak"):
        b.append((akun["pajak"], pajak, 0, f"PPN Retur - {no}"))
    if porsi_ar > 0:
        b.append((akun["ar"], 0, porsi_ar, f"Pengurangan Piutang - {no}"))
    if kelebihan > 0:
        b.append((akun["krd"], 0, kelebihan, f"Saldo kredit pelanggan dari {no}"))
    return b


def baris_hpp_nk(cn, restock: list) -> list:
    """[(account_id, debit, credit, memo)] jurnal CREDIT_NOTE_COGS == penulis: Dr Persediaan per baris · Cr HPP per akun (urutan pertama muncul)."""
    no = cn["credit_note_number"]
    b = [(r["inv"], r["line_cost"], 0, f"Retur HPP - {no} - {r['nama']}") for r in restock]
    ember = {}
    for r in restock:
        ember[r["cogs"]] = ember.get(r["cogs"], Decimal("0")) + r["line_cost"]
    return b + [(a, 0, t, f"Retur HPP - {no}") for a, t in ember.items()]


async def rencana_batal_nk(conn, ctx: dict, cn_id, alasan, pratinjau: bool = False) -> dict:
    """URUTAN == void_nota_kredit_core lama (sesudah kunci CREDIT_NOTE_VOID): ada (404) · [pratinjau: alasan wajib] · sudah batal · DRAF =
    DIHAPUS (bukan blok) · diterapkan · dikembalikan · saldo KRD dari NK ini sudah dipakai · periode hari ini · porsi Dimuka bisa dipulihkan."""
    from ..routers.customer_deposits import compute_deposit_remaining
    R = _nk()
    tid = ctx["tenant_id"]
    bl = Blok(pratinjau)
    cn = await _baca_nk(conn, tid, cn_id, bl)
    if pratinjau and not (alasan or "").strip():
        bl.galat("VOID_REASON_REQUIRED", 422, "Alasan pembatalan wajib diisi.")
    hasil = {"blocks": bl, "cn": cn, "hapus": False, "dep": None, "hari_ini": None}
    if cn["status"] == "void":
        bl.galat("CN_ALREADY_VOID", 400, "Nota kredit ini sudah dibatalkan.")
        return hasil
    if cn["status"] == "draft":
        hasil["hapus"] = True
        return hasil
    if (cn["amount_applied"] or 0) > 0:
        bl.galat("CN_HAS_APPLICATIONS", 400, "Nota kredit sudah diterapkan ke faktur. Lepas penerapannya dulu.")
    if (cn["amount_refunded"] or 0) > 0:
        bl.galat("CN_HAS_REFUNDS", 400, "Dana nota kredit ini sudah dikembalikan ke pelanggan, jadi tidak bisa dibatalkan.")
    if cn["created_deposit_id"]:
        dep = await conn.fetchrow(
            "SELECT id, deposit_number, amount, status FROM customer_deposits WHERE id = $1 AND tenant_id = $2"
            + ("" if pratinjau else " FOR UPDATE"), cn["created_deposit_id"], tid)
        hasil["dep"] = dep
        if dep and dep["status"] != "void" and await compute_deposit_remaining(conn, tid, dep["id"]) < Decimal(str(dep["amount"])):
            bl.galat("CN_CREDIT_USED", 400, (f"Saldo kredit {dep['deposit_number']} dari nota kredit ini sudah dipakai "
                                             "(diterapkan ke faktur atau dikembalikan). Lepaskan pemakaiannya dulu."))
    hari_ini = await R.tanggal_dokumen(conn, tid)
    hasil["hari_ini"] = hari_ini
    r = await conn.fetchrow("SELECT status FROM fiscal_periods WHERE tenant_id = $1 AND start_date <= $2 AND end_date >= $2", tid, hari_ini)
    if r and r["status"] != "OPEN":
        bl.galat("PERIOD_CLOSED", 400, tg.periode_tertutup(None, r["status"]))
    await bl.coba("CN_DEFERRED_RESTORE", R.cn_tertunda.pulihkan_saat_void(conn, tid, cn, periksa_saja=True, kunci=not pratinjau))
    return hasil


async def _jurnal_balik_nk(conn, tid: str, jid, deskripsi: str, source_type: str, tgl) -> dict:
    """Cermin jurnal (tukar D/K, memo 'Reversal - <memo>') -- kueri baris SAMA dengan penulis void (urutan baris ikut)."""
    baris = [(b["account_id"], b["credit"], b["debit"], f"Reversal - {b['memo'] or ''}")
             for b in await conn.fetch("SELECT * FROM journal_lines WHERE journal_id = $1", jid)]
    return {"journal_number": None, "source_type": source_type, "description": deskripsi, "journal_date": tgl.isoformat() if tgl else None,
            "lines": await _tampil(conn, tid, baris)}


async def pratinjau_post_void_nk(conn, ctx: dict, cn_id, aksi: str, alasan) -> dict:
    """aksi in (post, void). Pemanggil membuka transaksi READ ONLY. Bentuk respons == pratinjau lama (FE pratinjauAksiNk.ts):
    before/after/journals/invoice/payload; journal_number null; saldo kredit KRD baru tanpa nomor (deposit_number null)."""
    R = _nk()
    tid = ctx["tenant_id"]
    r = await (rencana_posting_nk(conn, ctx, cn_id, pratinjau=True) if aksi == "post" else rencana_batal_nk(conn, ctx, cn_id, alasan, pratinjau=True))
    bl, cn = r["blocks"], r["cn"]
    bisa = not bl.daftar
    sebelum = await R._keadaan_nk(conn, tid, cn_id)
    fid = sebelum.get("original_invoice_id")
    f0 = await R._sisa_faktur_nk(conn, tid, fid)
    sesudah, f1, jurnal = sebelum, f0, []
    if bisa and aksi == "post":
        akun = r["akun"]
        jurnal.append({"journal_number": None, "source_type": "CREDIT_NOTE",
                       "description": f"Credit Note {cn['credit_note_number']} - {cn['customer_name']}",
                       "journal_date": cn["credit_note_date"].isoformat(),
                       "lines": await _tampil(conn, tid, baris_posting_nk(cn, akun, r["u"], r["subtotal"], r["pajak"], r["porsi_ar"], r["kelebihan"]))})
        if r["restock"]:
            jurnal.append({"journal_number": None, "source_type": "CREDIT_NOTE_COGS",
                           "description": f"COGS reversal {cn['credit_note_number']} - {cn['customer_name']}",
                           "journal_date": cn["credit_note_date"].isoformat(), "lines": await _tampil(conn, tid, baris_hpp_nk(cn, r["restock"]))})
        st = "posted"
        sisa = R.sisa_kredit_nk(st, cn["total_amount"], cn["amount_applied"], cn["amount_refunded"], cn["original_invoice_id"])
        sesudah = {**sebelum, "status": st, "status_detail": R.status_detail_nk(st, cn["amount_applied"], cn["amount_refunded"]),
                   "remaining": float(sisa),
                   "customer_credit": ({"deposit_number": None, "status": "posted", "remaining": float(r["kelebihan"])}
                                       if r["kelebihan"] > 0 else None)}
        if fid is not None and f0 is not None:
            f1 = float(Decimal(str(f0)) - r["porsi_ar"])
    elif bisa and aksi == "void" and r["hapus"]:
        sesudah = {"exists": False}
    elif bisa and aksi == "void":
        if cn["journal_id"]:
            jurnal.append(await _jurnal_balik_nk(conn, tid, cn["journal_id"], f"Void {cn['credit_note_number']} - {cn['customer_name']}",
                                                 "CREDIT_NOTE", r["hari_ini"]))
        kom = await conn.fetchrow(
            """SELECT id FROM journal_entries WHERE tenant_id = $1 AND source_type = 'CREDIT_NOTE_COGS' AND source_id = $2
               AND status = 'POSTED' AND reversed_by_id IS NULL LIMIT 1""", tid, cn_id)
        if kom:
            jurnal.append(await _jurnal_balik_nk(conn, tid, kom["id"], f"Void COGS companion {cn['credit_note_number']}", "CREDIT_NOTE_COGS",
                                                 r["hari_ini"]))
        sesudah = {**sebelum, "status": "void", "status_detail": R.status_detail_nk("void", cn["amount_applied"], cn["amount_refunded"]),
                   "remaining": 0.0}
        if sebelum.get("customer_credit"):
            # saldo kredit KRD ikut dibatalkan: jurnal pembalik menghapus kewajibannya -> sisa 0 (diukur harness V1/V2)
            sesudah["customer_credit"] = {**sebelum["customer_credit"], "status": "void", "remaining": 0.0}
        if fid is not None and f0 is not None:
            f1 = float(Decimal(str(f0)) + await kredit_piutang_nk(conn, tid, cn_id))
    for k in (sebelum, sesudah):
        k.pop("original_invoice_id", None)
    return {"credit_note_id": str(cn_id), "action": aksi, "can_proceed": bisa, "blocks": bl.respons(),
            "before": sebelum, "after": sesudah, "journals": jurnal,
            "invoice": ({"invoice_id": str(fid), "remaining_before": f0, "remaining_after": f1} if fid else None),
            "payload": ({"reason": alasan} if aksi == "void" else {}), "preview": True}
