"""BUG-373: PATCH barang dengan conversions menonaktifkan semua konversi lalu memasang ulang; konversi tanpa id harus UPSERT (aktifkan ulang baris nonaktif),
bukan INSERT polos yang menabrak uq_unit_conversion (tenant, product, base_unit, conversion_unit)."""
import inspect
import re
from app.routers import items as IT


def _jalur_ubah():
    src = inspect.getsource(IT)
    a = src.index("# Update conversions if provided")
    return src[a:a + 4000]


def test_jalur_ubah_memasang_konversi_dengan_upsert_bukan_insert_polos():
    j = _jalur_ubah()
    m = re.search(r"INSERT INTO unit_conversions \(.*?\"\"\"", j, re.S)
    assert m, "INSERT konversi di jalur ubah tak ditemukan"
    sql = m.group(0)
    assert "ON CONFLICT (tenant_id, product_id, base_unit, conversion_unit) DO UPDATE" in sql
    assert "is_active = true" in sql                       # baris nonaktif/soft-delete hidup lagi
    for k in ("conversion_factor = EXCLUDED.conversion_factor", "purchase_price = EXCLUDED.purchase_price", "sales_price = EXCLUDED.sales_price"):
        assert k in sql


def test_kunci_konflik_sama_dengan_constraint_unik_skema():
    import pathlib
    mig = (pathlib.Path(__file__).parents[2].parent / "migrations" / "V194__create_unit_conversions_item_pricing.sql").read_text()
    assert "CONSTRAINT uq_unit_conversion UNIQUE(tenant_id, product_id, base_unit, conversion_unit)" in mig


def test_nonaktifkan_semua_tetap_ada_agar_konversi_yang_dibuang_hilang():
    j = _jalur_ubah()
    assert "UPDATE unit_conversions SET is_active = false WHERE product_id = $1 AND tenant_id = $2" in j
