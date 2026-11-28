"""Sensus pagar tenant (WORKSPACE 28 Nov) butir 2-3: tulis/baca lintas tenant ditutup dengan saringan tenant."""
from pathlib import Path

APP = Path(__file__).resolve().parents[2] / "app"


def _src(f):
    return (APP / f).read_text()


def test_konversi_satuan_hanya_milik_barang_tenant():
    assert "WHERE id = $6 AND tenant_id = $7 AND product_id = $8" in _src("routers/items.py")


def test_alur_chat_disaring_tenant():
    s = _src("services/unified_agent/workflow_engine.py")
    assert "WHERE chat_session_id = $1 AND workflow_type = $2 AND tenant_id = $3" in s
    assert "AND tenant_id = $4\n              AND status NOT IN ('active', 'failed')" in s
    assert "WHERE id = $4 AND tenant_id = $5" in s


def test_harga_beli_dan_akun_barang_dalam_tenant():
    s = _src("services/inventory_helpers.py")
    assert "FROM products WHERE id = $1 AND tenant_id = $2\",\n        product_id, tenant_id" in s
    assert "purchase_price, 0) FROM products WHERE id = $1 AND tenant_id = $2" in s
    assert "track_inventory FROM products WHERE id = $1 AND tenant_id = $2" in _src("routers/sales_receipts.py")
    assert "FROM products WHERE id = $1 AND tenant_id = $2\", k[\"product_id\"], tid" in _src("services/nota_debit_rencana.py")


def test_tagihan_join_dalam_tenant_tagihan():
    assert _src("services/bills_service.py").count("tenant_id = (SELECT b_t.tenant_id FROM bills b_t WHERE b_t.id = bi.bill_id)") == 6
