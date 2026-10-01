"""Surat Jalan PDF (1 Okt 2026): tanpa `SET LOCAL app.tenant_id = '<f-string>'` (gateway BYPASSRLS -> bukan pagar,
hanya permukaan injeksi); pagar = filter tenant EKSPLISIT di tiap join ber-tenant + item dari id SJ yang lolos filter.
Paritas nyata: 9 SJ kaos+grapgrap identik sebelum/sesudah."""
import inspect

from app.routers import deliveries as DL


def _src():
    return inspect.getsource(DL.muat_pdf_surat_jalan)


def test_tanpa_set_local_fstring():
    assert "SET LOCAL" not in _src().split('"""', 2)[2]  # di luar docstring


def test_join_ber_tenant_difilter_eksplisit():
    s = " ".join(_src().split())
    for pola in ("LEFT JOIN customers c ON c.id = si.customer_id AND c.tenant_id = f.tenant_id",
                 "LEFT JOIN warehouses w ON w.id = f.warehouse_id AND w.tenant_id = f.tenant_id",
                 "WHERE f.id = $1 AND f.tenant_id = $2 AND si.tenant_id = f.tenant_id",
                 "LEFT JOIN products p ON p.id = fi.product_id AND p.tenant_id = $2"):
        assert pola in s, pola


def test_item_dari_id_sj_yang_lolos_filter_tenant():
    s = " ".join(_src().split())
    assert 'ORDER BY fi.created_at """, row["id"], ctx["tenant_id"], )' in s
