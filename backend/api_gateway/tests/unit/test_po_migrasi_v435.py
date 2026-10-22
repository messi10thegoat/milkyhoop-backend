"""V435 (22 Okt 2026): DROP trg_update_po_totals (penimpa total PO, variabel BIGINT) -- total dihitung kode (services/po_hitung)."""
from pathlib import Path


def _d():
    return Path(__file__).resolve().parents[3] / "migrations"


def test_v435_drop_dan_rollback_memulihkan_bentuk_lama():
    s = (_d() / "V435__po_drop_trigger_total.sql").read_text()
    assert "DROP TRIGGER IF EXISTS trg_update_po_totals ON public.purchase_order_items" in s
    assert "DROP FUNCTION IF EXISTS public.update_purchase_order_totals()" in s
    rb = (_d() / "V435__po_drop_trigger_total_ROLLBACK.sql").read_text()
    assert "CREATE TRIGGER trg_update_po_totals" in rb and "v_total BIGINT" in rb


def test_kode_sudah_menghitung_total_sendiri_sebelum_trigger_dibuang():
    import inspect, os
    os.environ.setdefault("OPENAI_API_KEY", "sk-boneka-unit-test-tanpa-jaringan")
    from app.routers import purchase_orders as PO
    assert "_hitung_ulang_total(conn, po_id)" in inspect.getsource(PO.create_purchase_order)
    assert "_hitung_ulang_total(conn, po_id)" in inspect.getsource(PO.update_purchase_order)
