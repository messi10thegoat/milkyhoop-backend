"""V315 SO selesai otomatis — sisi aplikasi (26 Sep 2026). Perilaku DB diuji scripts/gate_v315.sh (DB scratch, kejadian nyata)."""
import inspect
import os

os.environ.setdefault("OPENAI_API_KEY", "sk-boneka-unit-test-tanpa-jaringan")

from app.routers import sales_orders as SO  # noqa: E402
from app.services import so_riwayat as SR  # noqa: E402
from app.schemas.sales_orders import SalesOrderDetail  # noqa: E402


def test_tutup_manual_menandai_manual_terminal():
    s = " ".join(inspect.getsource(SO.close_sales_order).split())
    assert "UPDATE sales_orders SET status = 'completed', completed_source = 'manual'" in s


def test_detail_membawa_sumber_selesai_aman_sebelum_migrasi():
    s = " ".join(inspect.getsource(SO.get_sales_order_detail).split())
    assert 'completed_source=order.get("completed_source")' in s
    assert SalesOrderDetail.model_fields["completed_source"].default is None


def test_riwayat_mengenal_kejadian_otomatis():
    assert SR.RINGKAS_AUDIT["SALES_ORDER_AUTO_COMPLETED"] == "Pesanan selesai otomatis"
    assert SR.RINGKAS_AUDIT["SALES_ORDER_REOPENED"] == "Pesanan dibuka kembali"
    assert SR.ENTITAS_AUDIT["sales_orders"] == "sales_order"  # entity_type yang ditulis fungsi DB
