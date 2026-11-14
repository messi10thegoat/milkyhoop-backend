"""bawaan_berlaku (14 Nov 2026): akun + pajak yang BERLAKU untuk satu barang, beserta sumbernya.

Kolom barang NULL atau SAMA dengan bawaan usaha sekarang -> sumber "bawaan_usaha" (backfill V072 mengisi kolom dengan bawaan lama; tanpa
aturan ini FE menampilkan "diganti" palsu). Kolom beda -> "barang". Hanya MEMBACA. Sumber "kategori" menyusul sesudah B1 kategori.
Akun pembelian: tak ada peran akun tenant untuknya -> tanpa bawaan usaha (null) sampai ada putusan (aturan akuntansi = tanya pemilik)."""
from typing import Optional

# butir -> (kolom barang, jenis) ; jenis "akun" dicari di peta akun, "pajak" di peta pajak
BUTIR = {
    "akun_penjualan": ("sales_account_id", "akun"),
    "akun_pembelian": ("purchase_account_id", "akun"),
    "akun_persediaan": ("inventory_account_id", "akun"),
    "akun_hpp": ("cogs_account_id", "akun"),
    "pajak_jual": ("sales_tax_id", "pajak"),
    "pajak_beli": ("purchase_tax_id", "pajak"),
}
PERAN_AKUN = {"akun_penjualan": "REVENUE_SALES_GOODS", "akun_persediaan": "INVENTORY_MERCHANDISE", "akun_hpp": "COGS_SALES"}


def berlaku(barang: dict, usaha: dict, akun: dict, pajak: Optional[dict] = None) -> dict:
    """barang = baris products; usaha = {butir: {id,kode,nama}|None}; akun/pajak = {id: {id,kode,nama}} untuk id kolom barang."""
    pajak = pajak or {}
    hasil = {}
    for butir, (kolom, jenis) in BUTIR.items():
        bu = usaha.get(butir)
        nilai = barang.get(kolom)
        nilai = str(nilai) if nilai is not None else None
        if nilai is None or (bu and nilai == bu["id"]):
            hasil[butir] = {**bu, "sumber": "bawaan_usaha"} if bu else None
            continue
        peta = akun if jenis == "akun" else pajak
        rinci = peta.get(nilai) or {"id": nilai, "kode": None, "nama": None}
        hasil[butir] = {**rinci, "sumber": "barang"}
    return hasil


async def muat_usaha(conn, tenant_id: str) -> dict:
    """Bawaan usaha sekarang: akun lewat account_roles, pajak lewat tax_codes.is_default per arah (output=jual, input=beli)."""
    usaha = {b: None for b in BUTIR}
    for r in await conn.fetch(
            """SELECT ar.role_key, c.id::text AS id, c.account_code AS kode, c.name AS nama FROM account_roles ar
               JOIN chart_of_accounts c ON c.id = ar.account_id AND c.tenant_id = ar.tenant_id
               WHERE ar.tenant_id = $1 AND ar.role_key = ANY($2::text[])""", tenant_id, list(PERAN_AKUN.values())):
        for butir, peran in PERAN_AKUN.items():
            if peran == r["role_key"]:
                usaha[butir] = {"id": r["id"], "kode": r["kode"], "nama": r["nama"]}
    for r in await conn.fetch(
            "SELECT id::text AS id, code AS kode, name AS nama, direction FROM tax_codes WHERE tenant_id = $1 AND is_default AND COALESCE(is_active, true)",
            tenant_id):
        butir = {"output": "pajak_jual", "input": "pajak_beli"}.get(r["direction"])
        if butir and usaha[butir] is None:
            usaha[butir] = {"id": r["id"], "kode": r["kode"], "nama": r["nama"]}
    return usaha


async def untuk_barang(conn, tenant_id: str, barang: dict) -> dict:
    usaha = await muat_usaha(conn, tenant_id)
    ids_akun = [str(barang[k]) for k, j in BUTIR.values() if j == "akun" and barang.get(k)]
    ids_pajak = [str(barang[k]) for k, j in BUTIR.values() if j == "pajak" and barang.get(k)]
    akun = {r["id"]: dict(r) for r in await conn.fetch(
        "SELECT id::text AS id, account_code AS kode, name AS nama FROM chart_of_accounts WHERE tenant_id = $1 AND id::text = ANY($2::text[])",
        tenant_id, ids_akun)} if ids_akun else {}
    pajak = {r["id"]: dict(r) for r in await conn.fetch(
        "SELECT id::text AS id, code AS kode, name AS nama FROM tax_codes WHERE tenant_id = $1 AND id::text = ANY($2::text[])",
        tenant_id, ids_pajak)} if ids_pajak else {}
    return berlaku(barang, usaha, akun, pajak)
