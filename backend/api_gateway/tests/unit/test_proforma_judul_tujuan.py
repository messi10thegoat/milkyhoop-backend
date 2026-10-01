"""Judul proforma per TUJUAN (1 Okt 2026, pemilik: proforma PELUNASAN atas SO belum berfaktur tercetak
"Tagihan Uang Muka"). DP -> Tagihan Uang Muka, TERMIN -> Tagihan Termin, PELUNASAN -> Tagihan Pelunasan;
catatan kaki "Dokumen ini adalah <judul> (proforma)" ikut. Literal dari permintaan pemilik, bukan dari kamus kode."""
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


def _render(tangkap, purpose, **x):
    d = {"proforma_number": "PRO-T", "proforma_date": "2026-10-01", "status": "issued", "amount": 1000000,
         "paid_amount": 0, "sales_order_number": "SO-T", "customer_name": "Pelanggan", "purpose": purpose,
         "order_total_amount": 3000000, "received_before": 1000000, "remaining_after_this": 0}
    d.update(x)
    PS.get_pdf_service().generate_proforma_pdf(d, {"name": "Usaha"})
    return tangkap["isi"]


def _judul(isi):
    return re.search(r'<h1 class="doc-title">([^<]+)</h1>', isi).group(1).strip()


@pytest.mark.parametrize("purpose,judul", [("DP", "Tagihan Uang Muka"), ("TERMIN", "Tagihan Termin"),
                                           ("PELUNASAN", "Tagihan Pelunasan"), ("pelunasan", "Tagihan Pelunasan")])
def test_judul_dan_catatan_sesuai_tujuan(tangkap, purpose, judul):
    isi = _render(tangkap, purpose)
    assert _judul(isi) == judul
    assert f"Dokumen ini adalah {judul.lower()} (proforma)" in " ".join(isi.split())


def test_pelunasan_tak_menyebut_dirinya_uang_muka(tangkap):
    isi = " ".join(_render(tangkap, "PELUNASAN").split())
    assert "Tagihan Uang Muka" not in isi and "tagihan uang muka" not in isi
    assert "Pelunasan yang Ditagih" in isi  # ringkasan sudah per tujuan sejak 28 Sep
    assert "Uang muka sudah diterima" in isi  # potongan DP diterima tetap tampil (benar untuk pelunasan)


def test_tujuan_kosong_tetap_uang_muka(tangkap):
    assert _judul(_render(tangkap, None)) == "Tagihan Uang Muka"
