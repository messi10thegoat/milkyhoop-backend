"""SO detail per baris: item_type + requires_fulfillment (26 Sep 2026, CW: sembunyikan "Kirim barang" untuk non-stok)."""
import inspect
import os

os.environ.setdefault("OPENAI_API_KEY", "sk-boneka-unit-test-tanpa-jaringan")

from app.routers import sales_orders as SO  # noqa: E402
from app.schemas.sales_orders import SalesOrderItemResponse  # noqa: E402


def test_kueri_detail_membaca_jenis_dan_lacak_stok_bertenant():
    s = " ".join(inspect.getsource(SO.get_sales_order_detail).split())
    assert "p.item_type AS product_item_type" in s
    assert "COALESCE(soi.perlu_kirim, p.track_inventory, false) AS requires_fulfillment" in s
    assert "LEFT JOIN products p ON p.id = soi.item_id AND p.tenant_id = $2" in s
    assert 'item_type=item["product_item_type"]' in s and 'requires_fulfillment=bool(item["requires_fulfillment"])' in s


def test_skema_bawaan_teks_bebas_tak_perlu_kirim():
    b = SalesOrderItemResponse(id="1", description="x", quantity=1, unit_price=1, line_total=1)
    assert b.item_type is None and b.requires_fulfillment is False
    assert SalesOrderItemResponse(id="1", description="x", quantity=1, unit_price=1, line_total=1,
                                  item_type="goods", requires_fulfillment=True).requires_fulfillment is True
