"""Nota Debit -- PENENTU per aksi (U3d, 23 Okt 2026; putusan MASTER: pola uang_muka_vendor_rencana / bayar_keluar_rencana, BUKAN tulis-lalu-
rollback seperti pratinjau NK). SATU definisi blok + RENCANA jurnal per aksi (posting, terapkan, lepas, kembalikan dana, batal), dipakai DUA jalur:
  * endpoint TULIS (routers/vendor_credits + services/nota_debit): dipanggil di transaksi tulis (sesudah kunci yang sama seperti dulu);
    blok PERTAMA = HTTPException yang SAMA PERSIS (status + detail, termasuk detail dict 409) dengan kode lama -> perilaku tulis tak berubah;
  * endpoint PRATINJAU: transaksi READ ONLY tanpa kunci/sequence; SEMUA blok dikumpulkan (200); jurnal = rencana tanpa nomor.
Baris jurnal posting = baris_posting_nd (fungsi YANG SAMA dipakai penulis). HPP retur barang dilacak = rencana baca-saja dengan fungsi baca
YANG SAMA dengan record_inventory_outbound (get_cost_for_sale + resolve_inventory_accounts, total = int(qty × unit_cost)); harness
membuktikan pratinjau == tertulis. Akuntansi retur (Cr 5-10300 + Dr HPP/Cr Persediaan) TIDAK diubah -- tiket ironlaws (tiket-terbuka)."""
from decimal import Decimal
from typing import Optional
from uuid import UUID

from fastapi import HTTPException

from . import nota_debit as ND
from . import teks_galat as tg
from . import teks_jurnal as TJ  # TEKS-JURNAL-NKND
from . import nota_debit_gudang as NG  # ND-GUDANG
from .role_resolver import AccountRole, resolve_account_id_by_role, resolve_account_id_by_role_if_pkp

NOL = Decimal("0")


class Blok:
    """Tulis (pratinjau=False): blok pertama dilempar persis seperti kode lama. Pratinjau: dikumpulkan {code, message}."""

    def __init__(self, pratinjau: bool):
        self.pratinjau, self.daftar = pratinjau, []

    def galat(self, code: str, status: int, detail) -> None:
        if not self.pratinjau:
            raise HTTPException(status_code=status, detail=detail)
        pesan = detail.get("message") if isinstance(detail, dict) else detail
        kode = detail.get("code", code) if isinstance(detail, dict) else code
        self.daftar.append({"code": kode, "message": str(pesan), "status": status})

    async def coba(self, code: str, aw):
        try:
            return await aw
        except HTTPException as e:
            self.galat(code, e.status_code, e.detail)
            return None

    def respons(self) -> list:
        return [{"code": b["code"], "message": b["message"]} for b in self.daftar]


def _f(v):
    return float(Decimal(str(v))) if v is not None else None


async def _tampil(conn, tid: str, baris: list) -> list:
    out = []
    for akun, d, k, memo in baris:
        r = await conn.fetchrow("SELECT account_code, name FROM chart_of_accounts WHERE id = $1 AND tenant_id = $2", akun, tid) if akun else None
        out.append({"account_code": r["account_code"] if r else None, "account_name": r["name"] if r else None,
                    "debit": _f(d), "credit": _f(k), "memo": memo})
    return out


async def _rencana_jurnal_nd(conn, tid: str, source_type: str, deskripsi: str, tgl, baris: list) -> dict:
    return {"journal_number": None, "source_type": source_type, "description": deskripsi,
            "journal_date": tgl.isoformat() if tgl else None, "lines": await _tampil(conn, tid, baris)}


def kalimat_nd_melebihi_sisa_tagihan(sisa, total, nomor_tagihan) -> str:
    """Cermin NK (credit_notes 'Melebihi sisa faktur ...'); tg.rp, sen tidak dibulatkan (Law 9)."""
    return f"Melebihi sisa tagihan {tg.rp(sisa)}: nota debit {tg.rp(total)} atas {nomor_tagihan or 'tagihan'}."


def kalimat_nd_refund_melebihi_sisa(jumlah, sisa) -> str:
    """Cermin NK CN_REFUND_MELEBIHI_SISA; tg.rp, sen tidak dibulatkan (Law 9)."""
    return f"Pengembalian dana {tg.rp(jumlah)} melebihi sisa nota debit {tg.rp(sisa)}."


async def periode_terbuka(conn, tid: str, tgl, bl: Blok) -> None:
    """== cek periode penulis ND (400; PERIODE-TEKS: kalimat tg.periode_tertutup)."""
    r = await conn.fetchrow("SELECT status FROM fiscal_periods WHERE tenant_id = $1 AND start_date <= $2 AND end_date >= $2", tid, tgl)
    if r and r["status"] != "OPEN":
        bl.galat("PERIODE_TERTUTUP", 400, tg.periode_tertutup(None, r["status"]))


# ------------------------------------------------------------------ nomor (P2P-3b)
SQL_PERKIRAAN_NOMOR_ND = """SELECT 'VC-' || TO_CHAR(tanggal_bisnis($1), 'YYMM') || '-' || LPAD((COALESCE((
    SELECT last_number FROM vendor_credit_sequences WHERE tenant_id = $1 AND year_month = TO_CHAR(tanggal_bisnis($1), 'YYYY-MM')), 0) + 1)::text,
    4, '0')"""


async def perkiraan_nomor_nd(conn, tid: str) -> str:
    """Nomor ND yang AKAN diberikan generate_vendor_credit_number(tid, 'VC') -- format & penghitung SAMA, tanpa menulis/mengunci.
    PERKIRAAN: penulis paralel bisa mengambilnya dulu (pola NetSuite: nomor final saat simpan)."""
    return await conn.fetchval(SQL_PERKIRAAN_NOMOR_ND, tid)


# ------------------------------------------------------------------ posting
async def baris_posting_nd(conn, tid: str, vc, ap_account_id, akun_kredit, potongan: bool) -> list:
    """[(account_id, debit, credit, memo, peran)] jurnal VENDOR_CREDIT: Dr Hutang total · Cr Retur/Diskon dasar · Cr PPN Masukan (PKP, pajak > 0).
    Non-PKP berpajak (aturan pemilik 4 Nov "kapitalisasi balik", cermin tagihan): PPN tak pernah jadi PPN Masukan -> Cr Retur/Diskon = total
    (termasuk PPN), tanpa baris PPN -- penentu SAMA tagihan_aksi.dasar_tagihan. `peran` 'pajak' menandai baris PPN (penulis butuh id barisnya)."""
    from . import tagihan_aksi as TA
    bulat = await TA.pembulatan_dokumen(conn, tid, vc["total_amount"])  # c-2 (V479): Dr Hutang = total BULAT, kredit = nilai baris asli
    total = bulat["total"]
    pajak = vc["tax_amount"] or 0
    akun_pajak = await resolve_account_id_by_role_if_pkp(conn, tid, AccountRole.VAT_INPUT) if pajak > 0 else None
    dasar, _kapital = TA.dasar_tagihan(total, pajak, akun_pajak, bulat["selisih"])
    baris = [(ap_account_id, total, 0, f"Pengurangan Hutang - {vc['credit_number']}", "hutang"),
             (akun_kredit, 0, dasar, f"{'Diskon Pembelian' if potongan else 'Retur Pembelian'} - {vc['credit_number']}", "kredit")]
    if pajak > 0 and akun_pajak is not None:
        baris.append((akun_pajak, 0, pajak, f"PPN Retur - {vc['credit_number']}", "pajak"))
    bb = TA.baris_pembulatan(bulat, -1) if bulat["akun"] else None
    if bb:
        baris.append((bb["account_id"], bb["debit"], bb["credit"], f"Pembulatan - {vc['credit_number']}", "pembulatan"))
    return baris


async def rencana_posting(conn, ctx, vc, pratinjau: bool = False, items: Optional[list] = None) -> dict:
    """URUTAN == post_vendor_credit (sesudah kunci + baca): kaitan tagihan draf (dibukukan? ≤ sisa) · status draf · akun peran · pagar potongan
    PPN (409) · akun dipetakan · periode. -> {blocks, ap, akun_kredit, potongan[, journals, stock, bills]}.
    items = baris ND MAYA (P2P-3b, HANYA pratinjau buat ND: vc belum ditulis, id acak) -> HPP/stok dari baris itu, bukan vendor_credit_items.
    Penulis memanggil TANPA items (perilaku tak berubah)."""
    from . import tagihan_aksi as TA
    tid = ctx["tenant_id"]
    bl = Blok(pratinjau)
    sisa_t = None
    bulat = await TA.pembulatan_dokumen(conn, tid, vc["total_amount"])  # c-2: total yang AKAN dibukukan (draf = mentah)
    if vc["status"] == "draft" and vc["original_bill_id"]:
        sisa_t = await ND.sisa_tagihan_jurnal(conn, tid, vc["original_bill_id"])
        if sisa_t is None:
            bl.galat("TAGIHAN_TIDAK_AKTIF", 400, f"Tagihan {vc['original_bill_number'] or ''} belum dibukukan atau sudah batal.")
        elif bulat["total"] > sisa_t:
            bl.galat("MELEBIHI_SISA_TAGIHAN", 400, kalimat_nd_melebihi_sisa_tagihan(sisa_t, bulat["total"], vc["original_bill_number"]))
    if vc["status"] != "draft":
        bl.galat("BUKAN_DRAF", 400, tg.tak_bisa_status("vc", vc["status"], "diposting", vc["credit_number"]))
    ap = await resolve_account_id_by_role(conn, tid, AccountRole.AP_TRADE)
    akun_kredit = await resolve_account_id_by_role(conn, tid, AccountRole.COGS_PURCHASE_RETURN)
    potongan = vc["reason"] in ND.ALASAN_POTONGAN
    await bl.coba("ND_POTONGAN_PPN_BELUM", ND.tolak_potongan_ber_ppn(conn, tid, vc["reason"], vc["tax_amount"], vc["original_bill_id"]))
    if potongan:
        akun_kredit = await resolve_account_id_by_role(conn, tid, ND.peran_kredit_nd(vc["reason"]))
    if not ap or not akun_kredit:
        bl.galat("PERAN_AKUN_BELUM_DIPETAKAN", 500, "Akun hutang / retur / diskon pembelian belum dipetakan.")
    if bulat["blok"]:
        bl.galat(bulat["blok"]["code"], 422, bulat["blok"]["message"])
    await periode_terbuka(conn, tid, vc["credit_date"], bl)
    # ND-GUDANG: gudang asal + sisa retur + stok (penulis: kunci tagihan asal + FOR UPDATE stok) -- penentu SATU dgn pratinjau
    keluar = [] if potongan else await NG.rencana_keluar(
        conn, tid, vc, items if items is not None else await conn.fetch(
            "SELECT * FROM vendor_credit_items WHERE vendor_credit_id = $1 ORDER BY line_number", vc["id"]), kunci=not pratinjau, bl=bl)
    hasil = {"blocks": bl, "ap": ap, "akun_kredit": akun_kredit, "potongan": potongan, "keluar": keluar, "bulat": bulat}
    if not pratinjau:
        return hasil
    jurnal, stok = [], []
    if ap and akun_kredit:
        try:
            baris = await baris_posting_nd(conn, tid, vc, ap, akun_kredit, potongan)
            jurnal.append(await _rencana_jurnal_nd(conn, tid, "VENDOR_CREDIT", TJ.nd(vc['credit_number'], vc['vendor_name']), vc["credit_date"],
                                        [b[:4] for b in baris]))
        except HTTPException as e:
            bl.galat("NON_PKP", e.status_code, e.detail)
    if not potongan:
        hpp, stok = await rencana_hpp(conn, tid, vc, keluar)
        jurnal += hpp
    tag = []
    if vc["original_bill_id"] and sisa_t is not None:
        tag = [{"bill_id": str(vc["original_bill_id"]), "bill_number": vc["original_bill_number"], "remaining_before": _f(sisa_t),
                "remaining_after": _f(sisa_t - bulat["total"])}]
    hasil.update({"journals": jurnal, "stock": stok, "bills": tag, "bank_transactions": []})
    return hasil


async def rencana_hpp(conn, tid: str, vc, keluar: list) -> tuple:
    """Rencana jurnal HPP + gerak stok retur barang dilacak, BACA-SAJA, fungsi baca YANG SAMA dengan record_inventory_outbound.
    keluar = NG.rencana_keluar (gudang asal; ND-GUDANG) -- gudang SAMA dengan penulis. Biaya = WAC saat ini (putusan B, Rule 3)."""
    from .inventory_helpers import get_cost_for_sale, resolve_inventory_accounts
    jurnal, stok = [], []
    for k in keluar:
        p = await conn.fetchrow("SELECT id, item_code, nama_produk, track_inventory FROM products WHERE id = $1", k["product_id"])
        biaya = await get_cost_for_sale(conn, tid, p["id"])
        total = int(float(k["qty"]) * float(biaya["unit_cost"]))
        stok.append({"product_id": str(p["id"]), "product": p["nama_produk"], "quantity": float(k["qty"]),
                     "unit_cost": _f(biaya["unit_cost"]), "total_cost": total, "cost_source": biaya["cost_source"],
                     "warehouse_id": str(k["gudang_id"]), "warehouse": k["gudang"]})
        if total > 0:
            akun = await resolve_inventory_accounts(conn, tid, p["id"])
            if akun["cogs_account_id"] and akun["inventory_account_id"]:
                jurnal.append(await _rencana_jurnal_nd(conn, tid, "VENDOR_CREDIT_COGS", TJ.nd_hpp(vc['credit_number']), vc["credit_date"],
                                            [(akun["cogs_account_id"], total, 0, "HPP Barang Dagang"),
                                             (akun["inventory_account_id"], 0, total, "Persediaan Barang Dagang")]))
    return jurnal, stok


# ------------------------------------------------------------------ terapkan / lepas (tanpa jurnal)
async def rencana_terapkan(conn, tid: str, vc_id, bill_id, jumlah, pratinjau: bool = False) -> dict:
    """URUTAN == nota_debit.terapkan_nota_debit (sesudah kunci): ada · sudah bertaut (409 dict) · terbit ber-jurnal · belum refund · seluruh nilai ·
    tagihan milik tenant & vendor (FOR UPDATE saat tulis) · pagar potongan PPN · tagihan aktif · ≤ sisa tagihan."""
    bl = Blok(pratinjau)
    vc = await conn.fetchrow("SELECT * FROM vendor_credits WHERE id = $1 AND tenant_id = $2" + ("" if pratinjau else " FOR UPDATE"), vc_id, tid)
    if not vc:
        bl.galat("TIDAK_DITEMUKAN", 404, "Nota debit tidak ditemukan.")
        return {"blocks": bl, "vc": None}
    if vc["original_bill_id"] is not None:
        bl.galat("ND_SUDAH_BERTAUT", 409, {"code": "ND_SUDAH_BERTAUT",
                                          "message": f"Nota debit ini sudah terkait ke tagihan {vc['original_bill_number'] or ''}".strip() + "."})
    if vc["status"] not in ("posted", "partial") or vc["journal_id"] is None:
        bl.galat("BELUM_DIBUKUKAN", 400, "Hanya nota debit yang sudah dibukukan yang bisa diterapkan ke tagihan.")
    if (vc["amount_received"] or 0) > 0:
        bl.galat("SUDAH_DIKEMBALIKAN", 400, "Nota debit yang dananya sudah dikembalikan vendor tidak bisa diterapkan ke tagihan.")
    total = Decimal(str(vc["total_amount"]))
    if Decimal(str(jumlah)) != total:
        bl.galat("HARUS_SELURUHNYA", 400, f"Nota debit harus diterapkan seluruhnya ({tg.rp(total)}) ke satu tagihan.")
    bill = await bl.coba("TAGIHAN_TIDAK_SAH", ND.tagihan_milik(conn, tid, bill_id, vc["vendor_id"], kunci=not pratinjau))
    sisa = None
    if bill:
        await bl.coba("ND_POTONGAN_PPN_BELUM", ND.tolak_potongan_ber_ppn(conn, tid, vc["reason"], vc["tax_amount"], bill["id"]))
        sisa = await ND.sisa_tagihan_jurnal(conn, tid, bill["id"])
        if sisa is None:
            bl.galat("TAGIHAN_TIDAK_AKTIF", 400, f"Tagihan {bill['invoice_number']} belum dibukukan atau sudah batal.")
        elif total > sisa:
            bl.galat("MELEBIHI_SISA_TAGIHAN", 400, kalimat_nd_melebihi_sisa_tagihan(sisa, total, bill["invoice_number"]))
    tag = [{"bill_id": str(bill["id"]), "bill_number": bill["invoice_number"], "remaining_before": _f(sisa),
            "remaining_after": _f(sisa - total)}] if bill and sisa is not None else []
    return {"blocks": bl, "vc": vc, "bill": bill, "total": total, "journals": [], "bank_transactions": [], "bills": tag, "stock": []}


async def rencana_lepas(conn, tid: str, vc_id, alasan: Optional[str], pratinjau: bool = False) -> dict:
    """URUTAN == nota_debit.lepas_nota_debit: alasan wajib (422) · ada · penerapan aktif. Tanpa jurnal."""
    bl = Blok(pratinjau)
    if not (alasan or "").strip():
        bl.galat("ALASAN_WAJIB", 422, "Alasan melepas penerapan wajib diisi.")
    vc = await conn.fetchrow("SELECT * FROM vendor_credits WHERE id = $1 AND tenant_id = $2" + ("" if pratinjau else " FOR UPDATE"), vc_id, tid)
    if not vc:
        bl.galat("TIDAK_DITEMUKAN", 404, "Nota debit tidak ditemukan.")
        return {"blocks": bl, "vc": None}
    app = await conn.fetchrow(
        """SELECT * FROM vendor_credit_applications WHERE vendor_credit_id = $1 AND tenant_id = $2 AND status = 'active'
           ORDER BY created_at DESC LIMIT 1""" + ("" if pratinjau else " FOR UPDATE"), vc_id, tid)
    if not app:
        bl.galat("TIDAK_DITERAPKAN", 400, "Nota debit ini tidak sedang diterapkan ke tagihan.")
    tag = []
    if app:
        sisa = await ND.sisa_tagihan_jurnal(conn, tid, app["bill_id"])
        if sisa is not None:
            tag = [{"bill_id": str(app["bill_id"]), "bill_number": app["bill_number"], "remaining_before": _f(sisa),
                    "remaining_after": _f(sisa + Decimal(str(app["amount_applied"])))}]
    return {"blocks": bl, "vc": vc, "app": app, "journals": [], "bank_transactions": [], "bills": tag, "stock": []}


# ------------------------------------------------------------------ kembalikan dana (refund)
async def rencana_kembali(conn, ctx, vc_id, body, pratinjau: bool = False) -> dict:
    """URUTAN == receive_refund: ada (404) · status terbit · bebas (tak bertaut) · ≤ sisa jurnal · rekening/akun · akun kas-bank · akun ada · periode.
    Jurnal Dr Kas-Bank / Cr Hutang; cermin bank 'deposit' bila rekening."""
    tid = ctx["tenant_id"]
    bl = Blok(pratinjau)
    vc = await conn.fetchrow("SELECT * FROM vendor_credits WHERE id = $1 AND tenant_id = $2", vc_id, tid)
    if not vc:
        bl.galat("TIDAK_DITEMUKAN", 404, "Nota debit tidak ditemukan.")
        return {"blocks": bl, "vc": None}
    if vc["status"] not in ("posted", "partial"):
        bl.galat("STATUS_TAK_SAH", 400, tg.tak_bisa_status("vc", vc["status"], "menerima pengembalian dana", vc["credit_number"]))
    if vc["original_bill_id"] is not None:
        bl.galat("ND_BERTAUT", 400, (
            f"Nota debit ini terkait tagihan {vc['original_bill_number'] or ''}; lepas penerapan dulu sebelum menerima pengembalian dana."))
    sisa = await ND.sisa_nd(conn, tid, vc_id)
    if Decimal(str(body.amount)) > sisa:
        bl.galat("MELEBIHI_SISA", 400, kalimat_nd_refund_melebihi_sisa(body.amount, sisa))
    akun_id = None
    if body.bank_account_id:
        ba = await conn.fetchrow("SELECT coa_id FROM bank_accounts WHERE id = $1 AND tenant_id = $2", UUID(body.bank_account_id), tid)
        if not ba:
            bl.galat("REKENING_TIDAK_DITEMUKAN", 400, "Rekening bank tidak ditemukan.")
        else:
            akun_id = ba["coa_id"]
    elif body.account_id:
        akun_id = UUID(body.account_id)
    else:
        bl.galat("AKUN_WAJIB", 400, "Rekening wajib dipilih.")
    akun = await conn.fetchrow(
        """SELECT id, account_code as code, name, account_type, is_cash, is_bank_account FROM chart_of_accounts
           WHERE id = $1 AND tenant_id = $2""", akun_id, tid) if akun_id else None
    if akun and not (akun["account_type"] == "ASSET" and (akun["is_cash"] or akun["is_bank_account"] or body.bank_account_id)):
        bl.galat("AKUN_BUKAN_KAS", 400, "Akun penerima pengembalian dana harus akun kas atau bank.")
    if akun_id and not akun:
        bl.galat("AKUN_TIDAK_DITEMUKAN", 400, "Rekening penerima tidak ditemukan.")
    await periode_terbuka(conn, tid, body.refund_date, bl)
    hasil = {"blocks": bl, "vc": vc, "akun_id": akun_id, "sisa": sisa}
    if not pratinjau:
        return hasil
    ap = await resolve_account_id_by_role(conn, tid, AccountRole.AP_TRADE)
    jurnal = [await _rencana_jurnal_nd(conn, tid, "VENDOR_CREDIT_REFUND", TJ.nd_refund(vc['credit_number'], vc['vendor_name']), body.refund_date,
                            baris_refund_nd(akun_id, ap, body.amount, vc["credit_number"]))] if akun_id and ap else []
    bank = []
    if body.bank_account_id and akun_id:
        nama = await conn.fetchval("SELECT account_name FROM bank_accounts WHERE id = $1 AND tenant_id = $2", UUID(body.bank_account_id), tid)
        bank = [{"bank_account": nama, "amount": _f(body.amount), "transaction_type": "deposit"}]
    hasil.update({"journals": jurnal, "bank_transactions": bank, "bills": [], "stock": []})
    return hasil


def baris_refund_nd(akun_kas, ap, jumlah, nomor: str) -> list:
    """Baris jurnal VENDOR_CREDIT_REFUND == penulis lama: Dr Kas-Bank / Cr Hutang."""
    return [(akun_kas, jumlah, 0, f"Terima Refund - {nomor}"), (ap, 0, jumlah, f"Hutang Refund - {nomor}")]


# ------------------------------------------------------------------ batal (void)
async def rencana_batal(conn, ctx, vc_id, alasan: Optional[str], pratinjau: bool = False) -> dict:
    """URUTAN == void_vendor_credit (sesudah kunci): ada (404) · sudah batal · DRAF = DIHAPUS (bukan blok; after.exists false) · penerapan aktif ·
    refund · periode hari ini (bila ber-jurnal). Pratinjau: alasan wajib (skema tulis mewajibkannya, min_length 1). Jurnal = cermin jurnal
    posting + cermin SETIAP jurnal VENDOR_CREDIT_COGS efektif (nota_debit.balik_hpp_stok_pajak)."""
    from ..utils.tanggal_tenant import tanggal_dokumen
    tid = ctx["tenant_id"]
    bl = Blok(pratinjau)
    if pratinjau and not (alasan or "").strip():
        bl.galat("VOID_REASON_REQUIRED", 422, "Alasan pembatalan wajib diisi.")
    vc = await conn.fetchrow("SELECT * FROM vendor_credits WHERE id = $1 AND tenant_id = $2", vc_id, tid)
    if not vc:
        bl.galat("TIDAK_DITEMUKAN", 404, "Nota debit tidak ditemukan.")
        return {"blocks": bl, "vc": None}
    if vc["status"] == "void":
        bl.galat("SUDAH_DIBATALKAN", 400, "Nota debit sudah dibatalkan.")
        return {"blocks": bl, "vc": vc, "hapus": False}
    if vc["status"] == "draft":
        return {"blocks": bl, "vc": vc, "hapus": True, "journals": [], "bank_transactions": [], "bills": [], "stock": []}
    if await conn.fetchval("SELECT 1 FROM vendor_credit_applications WHERE vendor_credit_id = $1 AND tenant_id = $2 AND status = 'active'",
                           vc_id, tid):
        bl.galat("MASIH_DITERAPKAN", 400, "Lepas penerapan nota debit dari tagihan dulu sebelum membatalkannya.")
    if await conn.fetchval("SELECT 1 FROM vendor_credit_refunds WHERE vendor_credit_id = $1 AND tenant_id = $2", vc_id, tid):
        bl.galat("SUDAH_DIKEMBALIKAN", 400, "Nota debit yang sudah menerima pengembalian dana tidak bisa dibatalkan.")
    hari_ini = None
    if vc["journal_id"]:
        hari_ini = await tanggal_dokumen(conn, tid)
        await periode_terbuka(conn, tid, hari_ini, bl)
    hasil = {"blocks": bl, "vc": vc, "hapus": False, "hari_ini": hari_ini}
    if not pratinjau:
        return hasil
    jurnal = []
    if vc["journal_id"]:
        asli = await conn.fetch("SELECT account_id, debit, credit, memo FROM journal_lines WHERE journal_id = $1", vc["journal_id"])
        jurnal.append(await _rencana_jurnal_nd(conn, tid, "VENDOR_CREDIT", TJ.nd_batal(vc['credit_number'], vc['vendor_name']), hari_ini,
                                    [(b["account_id"], b["credit"], b["debit"], f"Reversal - {b['memo'] or ''}") for b in asli]))
        for j in await conn.fetch(
                """SELECT id FROM journal_entries WHERE tenant_id = $1 AND source_type = 'VENDOR_CREDIT_COGS' AND source_id = $2
                   AND status = 'POSTED' AND reversed_by_id IS NULL AND reversal_of_id IS NULL ORDER BY created_at""", tid, vc_id):
            bj = await conn.fetch("SELECT account_id, debit, credit, memo FROM journal_lines WHERE journal_id = $1 ORDER BY line_number", j["id"])
            jurnal.append(await _rencana_jurnal_nd(conn, tid, "VENDOR_CREDIT_COGS", TJ.nd_batal_hpp(vc['credit_number']), hari_ini,
                                        [(b["account_id"], b["credit"], b["debit"], f"Pembalik - {b['memo'] or ''}") for b in bj]))
    hasil.update({"journals": jurnal, "bank_transactions": [], "bills": [], "stock": []})
    return hasil
