"""Sapuan tanggal bisnis B (26 Sep 2026): analitik persediaan per tanggal DOKUMEN + batas tanggal bisnis.

Putusan MASTER (a): top-products / slow-moving (inventory_ledger.movement_date) & product-margins
(sales_invoices.invoice_date) — dulu created_at (waktu input) vs date_trunc(CURRENT_DATE UTC); low-stock
days_since_movement dari tanggal bisnis. Batas bulan = rp_periode.batas_periode (lewat services/periode_laporan).
"""
import ast
from datetime import date
from pathlib import Path

import pytest

from app.services.periode_laporan import rentang_periode

SRC = (Path(__file__).resolve().parents[2] / "app/routers/inventory.py").read_text()


@pytest.mark.parametrize("period,hari,harap", [
    ("this_month", date(2026, 9, 26), (date(2026, 9, 1), date(2026, 9, 30))),
    ("last_month", date(2026, 9, 26), (date(2026, 8, 1), date(2026, 8, 31))),
    ("last_month", date(2026, 1, 5), (date(2025, 12, 1), date(2025, 12, 31))),   # lintas tahun
    ("last_month", date(2026, 3, 31), (date(2026, 2, 1), date(2026, 2, 28))),
    ("this_year", date(2026, 9, 26), (date(2026, 1, 1), date(2026, 12, 31))),
    ("all", date(2026, 9, 26), None),
    ("ngawur", date(2026, 9, 26), None),
])
def test_rentang(period, hari, harap):
    assert rentang_periode(period, hari) == harap


def _fungsi(nama):
    for n in ast.walk(ast.parse(SRC)):
        if isinstance(n, ast.AsyncFunctionDef) and n.name == nama:
            return n
    raise AssertionError(nama)


def _kode_tanpa_docstring(n):
    seg = ast.get_source_segment(SRC, n)
    doc = ast.get_docstring(n, clean=False) or ""
    return seg.replace(doc, "")


@pytest.mark.parametrize("nama", ["get_top_products", "get_slow_moving_products", "get_product_margins", "get_low_stock_alerts"])
def test_tanpa_tanggal_server_pakai_tanggal_bisnis(nama):
    n = _fungsi(nama)
    kode = _kode_tanpa_docstring(n)
    assert "CURRENT_DATE" not in kode and "date.today()" not in kode, nama
    assert any(isinstance(c, ast.Call) and getattr(c.func, "id", None) == "tanggal_dokumen" for c in ast.walk(n)), nama


@pytest.mark.parametrize("nama,kolom", [("get_top_products", "il.movement_date"),
                                        ("get_slow_moving_products", "il.movement_date"),
                                        ("get_product_margins", "si.invoice_date")])
def test_tanggal_dokumen_dan_rentang(nama, kolom):
    n = _fungsi(nama)
    kode = _kode_tanpa_docstring(n)
    assert f'"AND {kolom} BETWEEN $3::date AND $4::date" if rentang else ""' in kode
    assert "created_at >=" not in kode                                   # bukan lagi waktu input
    assert "*(rentang or ())" in kode
    assert any(isinstance(c, ast.Call) and getattr(c.func, "id", None) == "rentang_periode" for c in ast.walk(n))
