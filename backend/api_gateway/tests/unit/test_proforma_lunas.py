"""Proforma LUNAS (1 Okt 2026, MASTER/pemilik; pola faktur 27 Sep "Lunas: JANGAN Rp 0 sebagai angka besar").
Terbit + lunas -> angka besar = nominal tagihan proforma ini (label = judul), "Dibayar lunas <tgl>", stempel LUNAS,
ringkasan Sudah Dibayar / Sisa Rp 0, TANPA rekening. Tanggal hanya bila uang muka tertaut menutupinya (pemuat)."""
import re

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


def _pf(**x):
    d = {"proforma_number": "PRO-T", "proforma_date": "2026-10-01", "due_date": "2026-10-03", "status": "issued",
         "purpose": "PELUNASAN", "amount": 2405000, "paid_amount": 2405000, "outstanding_amount": 0,
         "is_paid": True, "paid_date": "2026-09-30", "sales_order_number": "SO-T", "customer_name": "Maria",
         "payment_bank_name": "BCA", "payment_account_number": "8295032185", "rekening_pemilik_cetak": "Pemilik"}
    d.update(x)
    return d


def _render(tangkap, d):
    PS.get_pdf_service().generate_proforma_pdf(d, {"name": "Usaha"})
    return " ".join(re.sub(r"<[^>]+>", " ", tangkap["isi"].split("<body>")[1]).split())


def test_lunas_angka_besar_nominal_bukan_rp0(tangkap):
    t = _render(tangkap, _pf())
    assert "TAGIHAN PELUNASAN" in t.upper() and "Rp 2.405.000 Dibayar lunas 30 Sep 2026" in t
    assert "LUNAS 30 SEP 2026" in t
    assert "Sudah Dibayar -Rp 2.405.000 Sisa Tagihan Ini Rp 0" in t
    assert "Mohon dibayar" not in t and "Rekening Pembayaran" not in t


def test_lunas_tanpa_tanggal_jujur(tangkap):
    t = _render(tangkap, _pf(paid_date=None))
    assert "Dibayar lunas" in t and "Dibayar lunas 30" not in t and "LUNAS" in t


def test_belum_lunas_tetap_seperti_dulu(tangkap):
    t = _render(tangkap, _pf(is_paid=False, paid_amount=0, outstanding_amount=2405000, paid_date=None))
    assert "Rp 2.405.000 Mohon dibayar sebelum 3 Okt 2026" in t
    assert "stempel-lunas" not in tangkap["isi"] and "Rekening Pembayaran" in t


def test_batal_tidak_berstempel_lunas(tangkap):
    _render(tangkap, _pf(status="cancelled", cancelled_at="2026-10-01T01:00:00Z"))
    assert "stempel-lunas" not in tangkap["isi"] and "DIBATALKAN" in tangkap["isi"]
