"""Geometri kertas HTML layar (1 Okt 2026): dari <head> + body kosong, di-cache tanpa <title>. Diukur: html_layar
200-430 ms -> ~1 ms; geometri setara tata letak penuh 41/41 dokumen nyata."""
import pytest

from app.services import pdf_service as PS

KOP = {"name": "Usaha", "address": None, "phone": None, "email": None, "logo_data": None}


def _kw(no="RCV-T"):
    return PS.get_pdf_service().render_receipt({"receipt_number": no, "amount": 1000, "amount_words": "Seribu Rupiah",
                                                "payer_name": "P", "status": "posted"}, KOP)


def _q():
    return PS.get_pdf_service().render_quote({"quote_number": "Q-T", "status": "sent", "items": [], "total_amount": 0}, KOP)


@pytest.mark.parametrize("buat", [_kw, _q])
def test_geometri_sama_dengan_tata_letak_penuh(buat):
    s = PS.get_pdf_service()
    r = buat()
    hal = s.dokumen(r).pages[0]
    b = hal._page_box
    penuh = tuple(round(x, 2) for x in (hal.width, hal.height, b.margin_top, b.margin_right, b.margin_bottom, b.margin_left))
    assert tuple(round(x, 2) for x in s.geometri(r)) == penuh


def test_geometri_dicache_lintas_dokumen_meski_judul_beda(monkeypatch):
    s = PS.get_pdf_service()
    s._GEOMETRI.clear()
    s.geometri(_kw("RCV-1"))
    panggil = []
    asli = s.dokumen
    monkeypatch.setattr(s, "dokumen", lambda r: panggil.append(1) or asli(r))
    s.geometri(_kw("RCV-2"))  # nomor (dan <title>) beda, kepala sama
    s.html_layar(_kw("RCV-3"))
    assert panggil == [], "tata letak dijalankan ulang -- cache tak kena"
