"""JUMLAH-BARIS-QUOTE (V427, 15 Okt 2026): Penawaran membawa jumlah baris yang diketik; kirim-ulang mewarisi;
konversi memakai jumlah tersimpan. Kesetaraan di data nyata: scripts/ops/uji-nyata-jumlah-baris-quote.py."""
import inspect
from decimal import Decimal as D

import pytest

from app.routers import quotes as QU
from app.schemas.quotes import QuoteItemCreate, QuoteItemResponse, QuoteItemUpdate
from app.services import sales_doc_calc as C


def _lama(**kw):
    b = {"id": "I1", "description": "Kaos", "quantity": D("26"), "unit_price": D("236538.46"), "jumlah_baris": D("6150000")}
    b.update(kw)
    return b


def test_kalkulator_penawaran_jumlah_diketik_dengan_diskon_fixed_dan_ppn():
    ln = {"quantity": D("26"), "unit_price": D("236538.46"), "discount_percent": 0, "tax_rate": 11, "line_amount": D("6150000")}
    r = C.compute_document([ln], doc_discount_amount=150000)
    assert r["total_amount"] == D("6660000.00")  # (6.150.000 - 150.000) x 1,11


def test_quote_line_membawa_jumlah_baris_tersimpan_dan_dipercaya():
    row = {"item_id": None, "description": "Kaos", "quantity": D("26"), "unit": None, "unit_price": D("236538.46"),
           "discount_percent": 0, "tax_id": None, "tax_rate": 0, "group_name": None, "sort_order": 0,
           "jumlah_baris": D("6150000")}
    ln = QU._quote_line(row)
    assert ln["jumlah_baris"] == D("6150000")
    assert C.compute_document([ln])["total_amount"] == D("6150000")  # bukan 6.149.999,96


def test_warisan_menurut_id():
    it = [{"id": "I1", "description": "Kaos", "quantity": D("26"), "unit_price": D("236538.46")}]
    QU._warisi_jumlah_baris_quote(it, [_lama()])
    assert it[0]["jumlah_baris"] == D("6150000")


def test_warisan_tanpa_id_menurut_keterangan_qty_harga():
    it = [{"description": "Kaos", "quantity": D("26"), "unit_price": D("236538.46")}]
    QU._warisi_jumlah_baris_quote(it, [_lama()])
    assert it[0]["jumlah_baris"] == D("6150000")


def test_warisan_sekali_pakai_dua_baris_kembar():
    it = [{"description": "Kaos", "quantity": D("26"), "unit_price": D("236538.46")},
          {"description": "Kaos", "quantity": D("26"), "unit_price": D("236538.46")}]
    QU._warisi_jumlah_baris_quote(it, [_lama()])
    assert it[0].get("jumlah_baris") == D("6150000") and it[1].get("jumlah_baris") is None


@pytest.mark.parametrize("ubah", [{"quantity": D("20")}, {"unit_price": D("236000")}])
def test_warisan_dilepas_bila_qty_atau_harga_berubah(ubah):
    it = [{"id": "I1", "description": "Kaos", "quantity": D("26"), "unit_price": D("236538.46"), **ubah}]
    QU._warisi_jumlah_baris_quote(it, [_lama()])
    assert it[0].get("jumlah_baris") is None


def test_line_amount_diketik_tidak_ditimpa_warisan():
    it = [{"id": "I1", "description": "Kaos", "quantity": D("26"), "unit_price": D("236538.46"), "line_amount": D("6150000.05")}]
    QU._warisi_jumlah_baris_quote(it, [_lama()])
    assert it[0].get("jumlah_baris") is None and it[0]["line_amount"] == D("6150000.05")


def test_skema_harga_desimal_dan_line_amount():
    c = QuoteItemCreate(description="Kaos", quantity=26, unit_price="236538.46", line_amount="6150000")
    assert c.unit_price == D("236538.46") and c.line_amount == D("6150000")
    assert QuoteItemUpdate(unit_price="1000").unit_price == D("1000")
    r = QuoteItemResponse(id="1", description="x", quantity=1, unit_price=236538.46, line_total=1, line_amount=6150000.0)
    assert r.unit_price == 236538.46 and r.line_amount == 6150000.0
    assert QuoteItemResponse(id="1", description="x", quantity=1, unit_price=1000, line_total=1).unit_price == 1000


def test_tiga_insert_quote_items_dan_dua_konversi_menulis_jumlah_baris():
    src = inspect.getsource(QU)
    assert src.count("INSERT INTO quote_items") == 3
    for blok in src.split("INSERT INTO quote_items")[1:]:
        assert "jumlah_baris" in blok[:400]
    for tabel in ("INSERT INTO sales_order_items", "INSERT INTO sales_invoice_items"):
        assert "jumlah_baris" in src.split(tabel)[1][:600]


def test_fungsi_mati_int_dihapus():
    assert not hasattr(QU, "calculate_item_totals") and not hasattr(QU, "calculate_quote_totals")  # Law 9: tak ada int() hidup
