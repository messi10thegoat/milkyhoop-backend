"""PDF: angka besar di kepala (hero) = SISA yang masih ditagih, bukan total (26 Sep 2026).

Audit PDF grapgrap (5 dok): faktur penjualan benar (hero = amount_due), tetapi faktur PEMBELIAN LUNAS
001-09-26 tampil "Rp 2.421.900 / Jatuh tempo 18 Okt 2026" di kepala walau Sisa tagihan Rp 0; proforma
sama kelasnya (hero = nominal penuh + "Mohon dibayar sebelum" walau sudah dibayar). Tes merender lewat
jalur NYATA (PDFService.generate_*_pdf + template Jinja) dengan HTML WeasyPrint diganti penangkap string.
"""
import re
from datetime import date

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


def _hero(isi):
    m = re.search(r'class="hero-amount">\s*([^<]*)<', isi)
    assert m, "hero tak ditemukan"
    return " ".join(m.group(1).split())


def _bill(total, paid):
    return {"invoice_number": "B-1", "issue_date": date(2026, 9, 18), "due_date": date(2026, 10, 18),
            "vendor": {"name": "Knitto"}, "tenant": {"name": "Grapgrap"}, "subtotal": total, "amount": total,
            "grand_total": total, "amount_paid": paid, "amount_due": total - paid, "status": "posted",
            "items": [{"product_name": "Kain", "qty": 1, "price": total, "total": total}]}


@pytest.mark.parametrize("total,paid,hero", [
    (2421900, 2421900, "Rp 0"),              # LUNAS -> nol, bukan total
    (2421900, 421900, "Rp 2.000.000"),       # sebagian -> sisa
    (630000, 0, "Rp 630.000"),               # belum dibayar -> total
])
def test_hero_faktur_pembelian_sisa(tangkap, total, paid, hero):
    PS.get_pdf_service().generate_bill_pdf(_bill(total, paid))
    assert _hero(tangkap["isi"]) == hero
    # baris Total di ringkasan tetap nominal penuh
    assert re.search(r"Total\s*</td>\s*<td[^>]*>\s*Rp\s*" + re.escape(f"{total:,}".replace(",", ".")), tangkap["isi"])


def _proforma(amount, paid):
    return {"proforma_number": "PRO-1", "proforma_date": date(2026, 9, 20), "due_date": date(2026, 9, 27),
            "purpose": "DP", "amount": amount, "paid_amount": paid, "outstanding_amount": amount - paid,
            "is_partially_paid": 0 < paid < amount, "status": "posted", "customer_name": "Agung"}


@pytest.mark.parametrize("amount,paid,hero", [
    (1500000, 0, "Rp 1.500.000"),
    (1500000, 500000, "Rp 1.000.000"),
    (1500000, 1500000, "Rp 0"),
])
def test_hero_proforma_sisa(tangkap, amount, paid, hero):
    PS.get_pdf_service().generate_proforma_pdf(_proforma(amount, paid), {"name": "Grapgrap"})
    assert _hero(tangkap["isi"]) == hero


def test_faktur_penjualan_tetap_pola_acuan():
    src = (PS.TEMPLATE_DIR / "sales_invoice.html").read_text()
    assert "invoice.amount_due if invoice.amount_due is defined" in src
