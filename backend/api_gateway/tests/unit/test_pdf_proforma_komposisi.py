"""PDF proforma: komposisi Nilai Pesanan = detail SO (30 Sep 2026, laporan pemilik PRO-2609-0069 / SO-2609-0031).

Dulu: baris barang 25 x 75.000 = 1.875.000 lalu langsung "Nilai Pesanan Rp 2.025.000" -> ongkir 150.000 tak
terlihat. Kini: barang -> Subtotal -> Diskon (bila ada) -> Ongkos kirim (bila > 0) -> PPN (bila ada) -> Nilai
Pesanan, angka dari kolom SO. Render lewat jalur NYATA (generate_proforma_pdf + template), HTML ditangkap.
"""
import re
from datetime import date
from pathlib import Path

import pytest

from app.services import pdf_service as PS


@pytest.fixture
def tangkap(monkeypatch):
    html = {}

    class _HTML:
        def __init__(self, string=None, **kw):
            html["isi"] = string

        def write_pdf(self, **kw):
            return b"%PDF"
    monkeypatch.setattr(PS, "HTML", _HTML)
    return html


def _pf(**kw):
    d = {"proforma_number": "PRO-2609-0069", "proforma_date": date(2026, 9, 30), "due_date": None,
         "sales_order_number": "SO-2609-0031", "customer_name": "Pelanggan", "purpose": "PELUNASAN",
         "amount": 2025000, "paid_amount": 0, "outstanding_amount": 2025000, "order_total_amount": 2025000,
         "order_items": [{"description": "kaos Pendek24s + Dtf 3 titik", "quantity": 25, "unit": "pcs",
                          "unit_price": 75000, "line_total": 1875000}],
         "order_subtotal": 1875000, "order_discount": 0, "order_shipping": 150000, "order_tax": 0,
         "received_before": 0, "remaining_after_this": 0, "is_partially_paid": False, "status": "issued"}
    d.update(kw)
    return d


def _baris_tabel(isi):
    """(keterangan, jumlah) per baris tabel item, urut, tanpa tag."""
    tbl = isi[isi.index('class="items-table"'):isi.index("</table>", isi.index('class="items-table"'))]
    keluar = []
    for tr in re.findall(r"<tr>(.*?)</tr>", tbl, re.S):
        sel = [" ".join(re.sub(r"<[^>]+>", "", c).split()) for c in re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)]
        if sel:
            keluar.append((sel[0], sel[-1]))
    return keluar


def test_pro_2609_0069_ongkir_terlihat_dan_berurutan(tangkap):
    PS.get_pdf_service().generate_proforma_pdf(_pf(), {"name": "Grapgrap"})
    b = _baris_tabel(tangkap["isi"])
    assert b[:4] == [("kaos Pendek24s + Dtf 3 titik", "Rp 1.875.000"), ("Subtotal", "Rp 1.875.000"),
                     ("Ongkos kirim", "Rp 150.000"), ("Nilai Pesanan", "Rp 2.025.000")]
    assert not any(k in ("Diskon", "PPN") for k, _ in b)


def test_diskon_ongkir_ppn_berurutan_dan_menjumlah(tangkap):
    PS.get_pdf_service().generate_proforma_pdf(_pf(
        order_subtotal=1000000, order_discount=100000, order_shipping=50000, order_tax=104500,
        order_total_amount=1054500, amount=500000, purpose="DP",
        order_items=[{"description": "Kaos", "quantity": 10, "unit": "pcs", "unit_price": 100000, "line_total": 1000000}],
    ), {"name": "Kaos Biru"})
    b = dict(_baris_tabel(tangkap["isi"]))
    urut = [k for k, _ in _baris_tabel(tangkap["isi"])]
    assert urut.index("Subtotal") < urut.index("Diskon") < urut.index("Ongkos kirim") < urut.index("PPN") < urut.index("Nilai Pesanan")
    assert (b["Subtotal"], b["Diskon"], b["Ongkos kirim"], b["PPN"], b["Nilai Pesanan"]) == \
        ("Rp 1.000.000", "-Rp 100.000", "Rp 50.000", "Rp 104.500", "Rp 1.054.500")
    # komposisi yang TERCETAK menjumlah ke Nilai Pesanan (pembaca bisa memeriksanya dari kertas)
    angka = lambda s: int(s.replace("Rp", "").replace(".", "").replace(" ", "").replace("-", ""))
    assert angka(b["Subtotal"]) - angka(b["Diskon"]) + angka(b["Ongkos kirim"]) + angka(b["PPN"]) == angka(b["Nilai Pesanan"])


def test_proforma_tanpa_so_tanpa_komposisi(tangkap):
    PS.get_pdf_service().generate_proforma_pdf(_pf(order_items=[], order_subtotal=None, order_shipping=None,
                                                    order_total_amount=None, sales_order_number=None), {"name": "X"})
    assert not any(k in ("Subtotal", "Ongkos kirim") for k, _ in _baris_tabel(tangkap["isi"]))


def test_router_membaca_kolom_so_bukan_menghitung_ulang():
    src = (Path(PS.__file__).parents[1] / "routers" / "proformas.py").read_text()
    blok = src[src.index("async def get_proforma_pdf("):]
    for kol in ("so.subtotal AS order_subtotal", "so.discount_amount AS order_discount",
                "so.shipping_amount AS order_shipping", "so.tax_amount AS order_tax"):
        assert kol in blok, kol
    assert '"order_shipping": _f(row["order_shipping"])' in blok
    assert '"line_total": _f(r["line_total"] - (r["tax_amount"] or 0))' in blok
