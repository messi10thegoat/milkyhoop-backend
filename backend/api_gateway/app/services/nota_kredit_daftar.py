"""Data daftar/detail NOTA KREDIT seragam dengan Pesanan/Faktur (S2-NK, 20 Okt 2026, MASTER). Server menghitung, FE memformat.

  sisa jurnal  = NK bebas (tanpa faktur) terbit: net Piutang (kredit - debit) jurnal EFEKTIF bersumber NK itu
                 (CREDIT_NOTE kredit; CREDIT_NOTE_REFUND debit). Draf / void / BERTAUT faktur = 0: menerapkan NK ke faktur
                 TIDAK membuat jurnal, aplikasi menulis original_invoice_id (pagar V250) dan kreditnya ikut faktur
                 (cabang 2 compute_ar_outstanding). Digerbang == sisa_kredit_nk (penjaga refund/apply, TIDAK diubah).
  aksi         = 'belum_terpakai' : terbit AND tak bertaut AND sisa jurnal > 0 (pill + ?aksi= + summary: predikat SAMA)
  sisa_keadaan = draf | utuh | sebagian | terpakai | dikembalikan | sebagian_dikembalikan | batal
  status_text  = teks_galat.status_id("cn", status_detail) == peta FE r288, KECUALI NK bertaut 'posted' -> "Terpakai"
                 (putusan MASTER 20 Okt: pola NetSuite Credit Memo "Fully Applied"; sisa 0, kredit sudah memotong faktur).
"""
from decimal import Decimal
from typing import Optional

AKSI_BELUM_TERPAKAI = "belum_terpakai"
AKSI_NK = (AKSI_BELUM_TERPAKAI,)
KEADAAN_NK = ("draf", "utuh", "sebagian", "terpakai", "dikembalikan", "sebagian_dikembalikan", "batal")
_KEADAAN_DARI_DETAIL = {"draft": "draf", "posted": "utuh", "partial": "sebagian", "applied": "terpakai",
                        "refunded": "dikembalikan", "partially_refunded": "sebagian_dikembalikan", "void": "batal"}


def sql_sisa_nk(a: str = "credit_notes") -> str:
    """Sisa JURNAL satu NK (ungkapan SQL; $1 = tenant). Kembaran Python: sisa_nk_jurnal_many."""
    return (f"(CASE WHEN {a}.status IN ('draft', 'void') OR {a}.original_invoice_id IS NOT NULL THEN 0 ELSE ("
            "SELECT COALESCE(SUM(jl.credit) - SUM(jl.debit), 0) FROM journal_entries je "
            "JOIN journal_lines jl ON jl.journal_id = je.id "
            "JOIN chart_of_accounts coa ON coa.id = jl.account_id AND coa.account_type = 'RECEIVABLE' "
            f"WHERE je.tenant_id = $1 AND je.source_id = {a}.id AND is_effective_journal(je.id)) END)")


async def sisa_nk_jurnal_many(conn, tenant_id: str, ids: list) -> dict:
    """{str(id): Decimal} sisa jurnal NK -- ungkapan SQL YANG SAMA (sql_sisa_nk)."""
    if not ids:
        return {}
    rows = await conn.fetch(
        f"SELECT credit_notes.id, {sql_sisa_nk()} AS sisa FROM credit_notes "
        "WHERE credit_notes.tenant_id = $1 AND credit_notes.id = ANY($2::uuid[])", tenant_id, list(ids))
    return {str(r["id"]): Decimal(str(r["sisa"])) for r in rows}


def sql_aksi(aksi: str, a: str = "credit_notes") -> str:
    if aksi != AKSI_BELUM_TERPAKAI:
        raise ValueError(aksi)
    return f"({a}.status NOT IN ('draft', 'void') AND {a}.original_invoice_id IS NULL AND {sql_sisa_nk(a)} > 0)"


def aksi_nk(status: str, original_invoice_id, sisa) -> Optional[str]:
    if status in ("draft", "void") or original_invoice_id is not None or sisa is None:
        return None
    return AKSI_BELUM_TERPAKAI if Decimal(str(sisa)) > 0 else None


def _detail_efektif(status: str, status_detail: str, original_invoice_id) -> str:
    # NK bertaut berstatus 'posted' = kredit sudah memotong faktur (sisa 0) -> tampil seperti 'applied'
    return "applied" if status == "posted" and original_invoice_id is not None else status_detail


def keadaan_nk(status: str, status_detail: str, original_invoice_id) -> str:
    sd = _detail_efektif(status, status_detail, original_invoice_id)
    return _KEADAAN_DARI_DETAIL.get(sd, sd)


def status_text_nk(status: str, status_detail: str, original_invoice_id) -> str:
    from . import teks_galat as tg
    return tg.status_id("cn", _detail_efektif(status, status_detail, original_invoice_id))


def keadaan_cocok_sisa(keadaan: str, total, sisa) -> bool:
    """Gerbang: keadaan konsisten dengan sisa JURNAL (draf: sisa jurnal 0, belum di buku)."""
    s, a = Decimal(str(sisa or 0)), Decimal(str(total or 0))
    if keadaan in ("draf", "batal", "terpakai", "dikembalikan"):
        return s == 0
    if keadaan == "utuh":
        return s == a and a > 0
    return 0 < s < a


async def kelompok_untuk(conn, tid: str, nota: list) -> dict:
    """{credit_note_id: [kelompok]} bundel NOTA KREDIT (S2-NK b, 20 Okt 2026), urutan = bundel SO/faktur: rekap · pnw · kw · ki · nk.
    rekap/pnw = SO lewat faktur asal; kw = uang muka KRD yang dibuat dari kelebihan NK ini (bila terbit); ki = surat jalan + faktur asal;
    nk = nota kredit ITU SENDIRI (apa pun statusnya). Pembentuk dokumen HANYA dari services/dokumen_bundel; di sini hanya PEMILIHAN id.
    Dipakai bundel /credit-notes/{id}/documents DAN documents[] daftar."""
    from . import dokumen_bundel as DB
    from .uang_muka_daftar import _urut
    ids = [n["id"] for n in nota]
    if not ids:
        return {}
    rows = {r["id"]: r for r in await conn.fetch(
        """SELECT cn.id, cn.original_invoice_id, cn.created_deposit_id, si.sales_order_id AS so_id FROM credit_notes cn
           LEFT JOIN sales_invoices si ON si.id = cn.original_invoice_id AND si.tenant_id = cn.tenant_id
           WHERE cn.tenant_id = $1 AND cn.id = ANY($2::uuid[])""", tid, ids)}
    so_ids = sorted({r["so_id"] for r in rows.values() if r["so_id"]})
    quote = {r["id"]: r["quote_id"] for r in await conn.fetch(
        "SELECT id, quote_id FROM sales_orders WHERE tenant_id = $1 AND id = ANY($2::uuid[])", tid, so_ids)} if so_ids else {}
    inv = sorted({r["original_invoice_id"] for r in rows.values() if r["original_invoice_id"]})
    fak = {d["id"]: d for d in await DB.faktur(conn, tid, inv)}
    sj_per_inv = {}
    if inv:
        for r in await conn.fetch("SELECT id, invoice_id FROM invoice_fulfillments WHERE tenant_id = $1 AND invoice_id = ANY($2::uuid[])",
                                  tid, inv):
            sj_per_inv.setdefault(r["invoice_id"], []).append(r["id"])
    sj = {d["id"]: d for d in await DB.surat_jalan(conn, tid, [i for v in sj_per_inv.values() for i in v])}
    kw = await DB.kwitansi_uang_muka(conn, tid, [r["created_deposit_id"] for r in rows.values() if r["created_deposit_id"]])
    sendiri = {d["id"]: d for d in await DB.nota_kredit(conn, tid, ids)}
    pnw_cache, hasil = {}, {}
    for cid in ids:
        r, kel = rows.get(cid), []
        if r and r["so_id"]:
            kel.append({"key": "rekap", "label": "Rekap", "docs": [DB.rekap(r["so_id"])]})
            q = quote.get(r["so_id"])
            if q:
                if q not in pnw_cache:
                    pnw_cache[q] = await DB.penawaran(conn, tid, q)
                if pnw_cache[q]:
                    kel.append({"key": "pnw", "label": "Penawaran", "docs": [dict(pnw_cache[q])]})
        if r and r["created_deposit_id"] in kw:
            kel.append({"key": "kw", "label": "Pembayaran", "planned": None, "docs": [kw[r["created_deposit_id"]]]})
        i = r["original_invoice_id"] if r else None
        ki = _urut([sj[str(x)] for x in sj_per_inv.get(i, []) if str(x) in sj]) if i else []
        ki += [fak[str(i)]] if i and str(i) in fak else []
        if ki:
            kel.append({"key": "ki", "label": "Kirim & Faktur", "docs": ki})
        if str(cid) in sendiri:
            kel.append({"key": "nk", "label": "Nota Kredit", "docs": [sendiri[str(cid)]]})
        hasil[cid] = kel
    return hasil
