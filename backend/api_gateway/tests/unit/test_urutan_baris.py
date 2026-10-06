"""Urutan baris dokumen stabil = urutan isian (6 Okt 2026, MASTER) + setelan pembuka/penutup bisa dikosongkan."""
import inspect
import pathlib
import re

from app.utils.urutan_baris import urutan_isian
from app.schemas.quotes import CreateQuoteRequest, QuoteItemCreate
from app.schemas.sales_orders import SalesOrderItemCreate
from app.routers import quotes as Q, sales_orders as SO, accounting_settings as AS

ITEM = {"description": "x", "quantity": 1, "unit_price": 1000}


def test_tanpa_sort_order_klien_jadi_indeks():
    its = [QuoteItemCreate(**ITEM), QuoteItemCreate(**ITEM), QuoteItemCreate(**ITEM)]
    assert [i.sort_order for i in its] == [0, 0, 0]  # bawaan skema = akar cacat
    assert urutan_isian(its) == [0, 1, 2]


def test_sort_order_klien_dihormati_campuran():
    its = [QuoteItemCreate(**ITEM, sort_order=5), QuoteItemCreate(**ITEM), SalesOrderItemCreate(**ITEM, sort_order=0)]
    assert urutan_isian(its) == [5, 1, 0]


def test_create_penawaran_dan_so_memakai_urutan_isian():
    for f in (Q.create_quote, SO.create_sales_order):
        src = inspect.getsource(f)
        assert "_urut = urutan_isian(body.items)" in src and "_urut[idx]" in src, f.__name__
        assert 'item.get("sort_order", idx)' not in src


def test_pembaca_baris_penjualan_berpenentu_stabil():
    app = pathlib.Path(Q.__file__).resolve().parents[1]
    buruk = []
    for f in ("routers/quotes.py", "routers/sales_orders.py", "routers/sales_invoices.py", "routers/proformas.py",
              "routers/anomalies.py", "routers/dokumen.py", "services/so_ubah_terkonfirmasi.py", "services/so_pengiriman.py"):
        for m in re.finditer(r"ORDER BY ([^\"\n]*sort_order[^\"\n]*)", (app / f).read_text()):
            k = m.group(1)
            if "{" in k:  # parameter daftar (ORDER BY {kolom} {sort_order}) -- bukan baris dokumen
                continue
            if not re.search(r"sort_order(\s+NULLS LAST)?\s*,\s*\w*\.?id\b", k):
                buruk.append((f, k.strip()))
    assert not buruk, buruk


def test_setelan_pembuka_penutup_bisa_dikosongkan():
    src = inspect.getsource(AS.update_accounting_settings)
    for k in ("default_quote_opening_text", "default_quote_closing_text"):
        assert f'"{k}" in data.model_fields_set' in src and f"if data.{k} is not None" not in src
