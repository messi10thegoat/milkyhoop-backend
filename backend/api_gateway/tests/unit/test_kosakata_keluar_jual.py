"""Kosakata keluar-jual inventory_ledger SATU sumber (26 Sep 2026) — services/kosakata_ledger.

Terukur: penjualan lewat Pengiriman = INVOICE_FULFILLMENT (kaos 5/5) tak terhitung di top-products/slow-moving
(daftar lama SALES_INVOICE/POS_SALE/CASH_SALE/SALES_RECEIPT_COGS); label riwayat & nama pelanggan hanya untuk
SALES_INVOICE. Putusan MASTER: analitik BERSIH dari pembatalan jual (*_VOID).
"""
import ast
from pathlib import Path

import pytest

from app.services import kosakata_ledger as KL

APP = Path(__file__).resolve().parents[2] / "app"


def test_konstanta():
    assert set(KL.KELUAR_JUAL_FAKTUR) <= set(KL.KELUAR_JUAL)
    assert "INVOICE_FULFILLMENT" in KL.KELUAR_JUAL
    assert "CASH_SALE" not in KL.KELUAR_JUAL and "SALES_RECEIPT_COGS" not in KL.KELUAR_JUAL  # tanpa penulis / jurnal
    assert KL.BATAL_JUAL == ("SALES_INVOICE_VOID", "INVOICE_FULFILLMENT_VOID", "POS_SALE_VOID")
    assert len(set(KL.DIKENAL)) == len(KL.DIKENAL)
    assert KL.sql_daftar(("A_B", "C")) == "('A_B', 'C')"
    with pytest.raises(AssertionError):
        KL.sql_daftar(("x'; DROP",))


def _modul(rel):
    s = (APP / rel).read_text()
    return s, ast.parse(s)


def test_pembatal_sepola_penulis():
    s, _ = _modul("services/inventory_helpers.py")
    assert 'void_source_type = f"{source_type}_VOID"' in s


HELPER = {"record_inventory_outbound", "record_inventory_inbound", "record_inventory_reversal"}


def test_penulis_lewat_helper_terklasifikasi():
    """Setiap source_type literal yang dikirim ke helper ledger WAJIB ada di DIKENAL (penulis baru -> merah)."""
    tak = []
    for p in APP.rglob("*.py"):
        s = p.read_text()
        try:
            t = ast.parse(s)
        except SyntaxError:
            continue
        for n in ast.walk(t):
            if isinstance(n, ast.Call) and getattr(n.func, "id", getattr(n.func, "attr", None)) in HELPER:
                for k in n.keywords:
                    if k.arg == "source_type" and isinstance(k.value, ast.Constant) and k.value.value not in KL.DIKENAL:
                        tak.append((str(p.relative_to(APP)), k.value.value))
    assert not tak, tak


# Fungsi yang MENULIS inventory_ledger lewat SQL mentah (terukur 26 Sep). Fungsi BARU -> merah sampai ditinjau
# & source_type-nya diklasifikasi di services/kosakata_ledger.
PENULIS_SQL = {
    ("routers/inventory.py", "add_product"), ("routers/items.py", "create_item"),
    ("routers/items.py", "create_stock_adjustment"), ("routers/items.py", "create_single_item_stock_transfer"),
    ("routers/production.py", "_reverse_inventory_ledger"), ("routers/production.py", "issue_materials"),
    ("routers/sales_invoices.py", "_execute_fulfillment"), ("routers/sales_receipts.py", "create_sales_receipt"),
    ("routers/sales_receipts.py", "void_sales_receipt"), ("routers/stock_adjustments.py", "post_stock_adjustment"),
    ("routers/stock_transfers.py", "cancel_stock_transfer"), ("routers/transactions.py", "_create_pos_inventory_and_journals"),
    ("services/inventory_helpers.py", "record_inventory_outbound"), ("services/inventory_helpers.py", "record_inventory_inbound"),
    ("services/inventory_helpers.py", "record_inventory_reversal"),
    ("services/kernel_document_executor.py", "_create_inventory_movements"),
}


def test_penulis_sql_mentah_dikenal():
    dapat = set()
    for p in APP.rglob("*.py"):
        s = p.read_text()
        try:
            t = ast.parse(s)
        except SyntaxError:
            continue
        for n in ast.walk(t):
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if any(isinstance(c, ast.Constant) and isinstance(c.value, str) and "INSERT INTO inventory_ledger" in c.value
                       for c in ast.walk(n)):
                    dapat.add((str(p.relative_to(APP)), n.name))
    baru = {d for d in dapat if d not in PENULIS_SQL}
    assert not baru, f"penulis inventory_ledger BARU — klasifikasikan source_type-nya di kosakata_ledger: {sorted(baru)}"


@pytest.mark.parametrize("rel", ["routers/inventory.py", "routers/items.py"])
def test_pembaca_tanpa_kosakata_basi(rel):
    s, _ = _modul(rel)
    for basi in ("'CASH_SALE'", "'SALES_RECEIPT_COGS'", "IN ('SALES_INVOICE', 'SALE')", "= 'SALES_INVOICE_VOID'",
                 "WHEN il.source_type = 'SALES_INVOICE' THEN", "ON il.source_type = 'SALES_INVOICE'"):
        assert basi not in s, (rel, basi)


def test_top_dan_slow_bersih_dari_pembatalan():
    s, t = _modul("routers/inventory.py")
    for nama in ("get_top_products", "get_slow_moving_products"):
        n = next(x for x in ast.walk(t) if isinstance(x, ast.AsyncFunctionDef) and x.name == nama)
        seg = " ".join(ast.get_source_segment(s, n).split())
        assert "SUM(il.quantity_out) FILTER (WHERE il.source_type IN {sql_daftar(KELUAR_JUAL)})" in seg, nama
        assert "- COALESCE(SUM(il.quantity_in) FILTER (WHERE il.source_type IN {sql_daftar(BATAL_JUAL)}), 0) AS total_qty_sold" in seg, nama


def test_profitabilitas_pakai_konstanta():
    for rel in ("services/profitability_query.py", "services/profitability_reconciliation.py"):
        s, _ = _modul(rel)
        assert "IN ('SALES_INVOICE', 'INVOICE_FULFILLMENT')" not in s.split('"""', 2)[2]      # di luar docstring modul
        assert 'sql_daftar(KELUAR_JUAL_FAKTUR)' in s
