"""
Penjaga satuan komponen BOM (satu-satunya sumber kebenaran).

Kelas kegagalan yang ditutup: kolom `bom_components.unit` adalah teks bebas dan
TIDAK ADA konversi satuan di jalur pengeluaran bahan
(`POST /api/production/{order_id}/issue-materials` memakai `quantity` apa adanya).
Akibatnya komponen yang ditulis `250` dengan satuan `g` untuk bahan yang distok
dalam `kg` mengeluarkan 250 kg dari gudang dan membebani WIP 1000x lipat, tanpa galat.

Keputusan:
- Satuan komponen WAJIB sama dengan `products.base_unit` milik komponen tersebut.
- Perbandingan case-insensitive + abaikan spasi di ujung ("KG" == "kg " == "kg").
- TIDAK PERNAH menebak/melakukan konversi diam-diam (250 g JANGAN jadi 0,25 kg).
- `unit` kosong/None -> diisi otomatis dari `base_unit`.
- `base_unit` kosong/None di master barang -> validasi DILEWATI (jangan memblokir
  kerja owner atas data master yang belum lengkap).
"""

from typing import Optional
from uuid import UUID

from fastapi import HTTPException


def _norm(value: Optional[str]) -> str:
    """Normalisasi satuan untuk perbandingan: buang spasi ujung, jadikan huruf kecil."""
    if value is None:
        return ""
    return value.strip().lower()


async def resolve_component_unit(
    conn,
    tenant_id,
    component_product_id: UUID,
    unit: Optional[str],
) -> Optional[str]:
    """
    Kembalikan satuan yang BOLEH disimpan untuk satu komponen BOM.

    - cocok  -> kembalikan `unit` apa adanya (nilai yang dikirim pemanggil)
    - kosong -> kembalikan `base_unit` (isi otomatis)
    - beda   -> HTTPException 400 dengan pesan yang menyebut KEDUA satuan

    Dipanggil dari SEMUA jalur tulis ke `bom_components`.
    """
    row = await conn.fetchrow(
        "SELECT nama_produk, base_unit FROM products WHERE tenant_id = $1 AND id = $2",
        tenant_id,
        component_product_id,
    )
    if row is None:
        raise HTTPException(
            status_code=400,
            detail=f"Barang komponen tidak ditemukan: {component_product_id}",
        )

    base_unit = row["base_unit"]
    product_name = row["nama_produk"] or str(component_product_id)

    # base_unit belum diisi di master barang -> lewati validasi, jangan blokir owner
    if _norm(base_unit) == "":
        return unit

    # unit tidak dikirim / kosong -> isi otomatis dari base_unit
    if _norm(unit) == "":
        return base_unit

    if _norm(unit) != _norm(base_unit):
        raise HTTPException(
            status_code=400,
            detail=(
                f"Satuan komponen '{unit}' tidak cocok dengan satuan dasar barang "
                f"'{product_name}' yaitu '{base_unit}'. Gunakan '{base_unit}'."
            ),
        )

    return unit
