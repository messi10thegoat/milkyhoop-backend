"""Nomor dokumen SATU aturan untuk Faktur, Pesanan Penjualan, Penawaran, Tagihan, Pesanan Pembelian (BUG-149, putusan pemilik 12 Nov 2026).

- Nomor tampil di form terisi nomor BERIKUTNYA (`pratinjau`: perkiraan, hanya baca, tak menaikkan seri, meniru generator DB
  yang melompati nomor terpakai); boleh diubah manual.
- Manual: trim + format (1-50, huruf/angka/-/./, tanpa spasi); terpakai di tenant ini -> 409 {code, message} dari server.
  Nomor manual TIDAK pernah menaikkan seri otomatis (pola Accurate/QBO); bila seri kelak sampai di nomor yang sudah dipakai,
  generator melewatinya (nomor tak pernah ganda).
- Balapan serentak: UNIQUE (tenant_id, nomor) = penegak akhir -> `dari_pelanggaran_unik` memetakan HANYA indeks nomor ke 409 yang
  sama (pelanggaran unik lain tidak disamarkan jadi 'nomor terpakai').
Duplikat peka huruf besar-kecil, sama dengan indeks (putusan MASTER 12 Nov; tak peka = indeks baru = migrasi)."""
import re
from typing import NamedTuple, Optional

from asyncpg import UniqueViolationError
from fastapi import HTTPException

_FORMAT = re.compile(r"^[A-Za-z0-9./\-]{1,50}$")
_NOMOR_DI_DETAIL = re.compile(r"\)=\([^,]*, (.+)\) already exists")


class Jenis(NamedTuple):
    awalan: str     # == awalan di fungsi generator DB
    seri: str       # tabel penghitung per (tenant_id, year_month)
    tabel: str
    kolom: str
    generator: str  # SQL generator bawaan (argumen $1 = tenant)
    indeks: str     # UNIQUE (tenant_id, kolom)


JENIS = {
    "sales_invoice": Jenis("INV", "sales_invoice_sequences", "sales_invoices", "invoice_number",
                           "SELECT generate_sales_invoice_number($1, 'INV')", "uq_sales_invoices_tenant_number"),
    "sales_order": Jenis("SO", "sales_order_sequences", "sales_orders", "order_number",
                         "SELECT generate_sales_order_number($1, 'SO')", "uq_sales_orders_number"),
    "quote": Jenis("QUO", "quote_sequences", "quotes", "quote_number",
                   "SELECT generate_quote_number($1, 'QUO')", "uq_quotes_number"),
    # Tagihan v2 (CW) = PB-; v1 dan PO->tagihan = BILL- (generator lain, SATU penghitung bill_number_sequences)
    "bill": Jenis("PB", "bill_number_sequences", "bills", "invoice_number",
                  "SELECT generate_purchase_bill_number($1)", "uq_bills_tenant_invoice"),
    "purchase_order": Jenis("PO", "purchase_order_sequences", "purchase_orders", "po_number",
                            "SELECT generate_purchase_order_number($1, 'PO')", "uq_po_tenant_number"),
}
GENERATOR_TAGIHAN_V1 = "SELECT generate_bill_number($1, 'BILL')"
_BATAS_LOMPAT = 100000  # == batas percobaan generator DB


def galat_terpakai(nomor: str) -> HTTPException:
    return HTTPException(status_code=409, detail={
        "code": "NOMOR_DOKUMEN_TERPAKAI",
        "message": f"Nomor {nomor} sudah dipakai di tenant ini. Pakai nomor lain atau kosongkan untuk nomor otomatis."})


def bersihkan(raw) -> Optional[str]:
    """None / '' / spasi -> None (otomatis). Selain itu trim + validasi format; salah -> 400."""
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    if not _FORMAT.match(s):
        raise HTTPException(status_code=400,
                            detail="Nomor dokumen tidak valid: 1–50 karakter, hanya huruf/angka/-/./ tanpa spasi.")
    return s


async def pastikan_bebas(conn, tenant_id: str, jenis: str, nomor: str, kecuali_id=None) -> None:
    """409 bila nomor sudah dipakai dokumen lain di tenant ini (`kecuali_id` = dokumen yang sedang diubah)."""
    j = JENIS[jenis]
    sql = f"SELECT 1 FROM {j.tabel} WHERE tenant_id = $1 AND {j.kolom} = $2"
    args = [tenant_id, nomor]
    if kecuali_id is not None:
        sql += " AND id <> $3"
        args.append(kecuali_id)
    if await conn.fetchval(sql, *args):
        raise galat_terpakai(nomor)


async def tetapkan_nomor(conn, tenant_id: str, jenis: str, manual, generator: Optional[str] = None) -> str:
    """Nomor dokumen yang dipakai saat BUAT: manual (valid, bebas) atau otomatis dari generator DB (menaikkan seri)."""
    nomor = bersihkan(manual)
    if nomor is None:
        return await conn.fetchval(generator or JENIS[jenis].generator, tenant_id)
    await pastikan_bebas(conn, tenant_id, jenis, nomor)
    return nomor


async def pratinjau(conn, tenant_id: str, jenis: str) -> str:
    """Nomor otomatis BERIKUTNYA: rumus SAMA dengan generator DB (seri per tenant + bulan tanggal bisnis, nomor terpakai
    dilompati), dibaca saja, seri TAK dinaikkan. PERKIRAAN: penulis paralel bisa mengambilnya dulu."""
    j = JENIS[jenis]
    bl = await conn.fetchrow("SELECT TO_CHAR(tanggal_bisnis($1), 'YYYY-MM') AS ym, TO_CHAR(tanggal_bisnis($1), 'YYMM') AS yymm", tenant_id)
    last = await conn.fetchval(f"SELECT last_number FROM {j.seri} WHERE tenant_id = $1 AND year_month = $2", tenant_id, bl["ym"])
    awalan = f"{j.awalan}-{bl['yymm']}-"
    terpakai = {r["n"] for r in await conn.fetch(
        f"SELECT {j.kolom} AS n FROM {j.tabel} WHERE tenant_id = $1 AND {j.kolom} LIKE $2", tenant_id, awalan + "%")}
    n = (last or 0) + 1
    for _ in range(_BATAS_LOMPAT):
        calon = f"{awalan}{n:04d}"
        if calon not in terpakai:
            return calon
        n += 1
    raise HTTPException(status_code=500, detail="Gagal menemukan nomor bebas.")


def dari_pelanggaran_unik(e: BaseException) -> Optional[HTTPException]:
    """409 bila `e` = pelanggaran UNIQUE pada indeks nomor salah satu dokumen (balapan serentak); selain itu None (biarkan naik)."""
    if not isinstance(e, UniqueViolationError) or getattr(e, "constraint_name", None) not in {j.indeks for j in JENIS.values()}:
        return None
    m = _NOMOR_DI_DETAIL.search(getattr(e, "detail", None) or "")
    return galat_terpakai(m.group(1) if m else "dokumen")
