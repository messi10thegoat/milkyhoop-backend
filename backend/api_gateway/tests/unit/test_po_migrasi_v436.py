"""V436 (22 Okt 2026): bill_items.purchase_order_item_id -- tautan baris tagihan -> baris PO (ADITIF, NULL-able)."""
from pathlib import Path


def _d():
    return Path(__file__).resolve().parents[3] / "migrations"


def test_v436_aditif_dan_rollback():
    s = (_d() / "V436__bill_items_tautan_po.sql").read_text()
    assert "ADD COLUMN IF NOT EXISTS purchase_order_item_id uuid" in s and "ON DELETE SET NULL" in s and "NOT NULL" not in s.replace("IS NOT NULL", "")
    assert "WHERE purchase_order_item_id IS NOT NULL" in s
    assert "DROP COLUMN IF EXISTS purchase_order_item_id" in (_d() / "V436__bill_items_tautan_po_ROLLBACK.sql").read_text()
