"""P3b SO-dokumen: semua dokumen SO memakai Inter (putusan pemilik 1 Okt 2026), dibuktikan dari PDF sungguhan.

Kelas cacat yang dijaga: @font-face tanpa FontConfiguration DIABAIKAN WeasyPrint tanpa galat -> DejaVu diam-diam
(keadaan sebelum P3b: invoice.css meminta 'Inter' tapi semua PDF tercetak DejaVu). Font dibaca dari BaseFont di PDF.
"""
import re
import zlib

import pytest

from app.services import pdf_service as PS

KOP = {"name": "TES", "address": None, "phone": None, "email": None, "logo_data": None}


def _font_pdf(pdf: bytes) -> set:
    """BaseFont dari PDF; WeasyPrint memadatkan objek ke object stream (Flate), jadi stream dibuka dulu."""
    teks = [pdf]
    for m in re.finditer(rb"stream\r?\n(.*?)\r?\nendstream", pdf, re.S):
        try:
            teks.append(zlib.decompress(m.group(1)))
        except zlib.error:
            pass
    return {f.decode() for t in teks for f in re.findall(rb"/BaseFont\s*/[A-Z]{6}\+([A-Za-z\-]+)", t)}


def _render(jenis):
    s = PS.get_pdf_service()
    if jenis == "kwitansi":
        return s.generate_receipt_pdf({"receipt_number": "RCV-T", "amount": 1000, "amount_words": "Seribu Rupiah",
                                       "payer_name": "Pelanggan", "status": "posted"}, KOP)
    if jenis == "penawaran":
        return s.generate_quote_pdf({"quote_number": "QUO-T", "status": "sent", "items": [], "total_amount": 0}, KOP)
    if jenis == "proforma":
        return s.generate_proforma_pdf({"proforma_number": "PRO-T", "purpose": "DP", "status": "issued",
                                        "amount": 1000, "order_items": []}, KOP)
    if jenis == "surat_jalan":
        return s.generate_delivery_note_pdf({"delivery_number": "SJ-T", "items": [], "tenant": KOP})
    raise AssertionError(jenis)


@pytest.mark.parametrize("jenis", ["kwitansi", "penawaran", "proforma", "surat_jalan"])
def test_dokumen_so_tercetak_inter_bukan_dejavu(jenis):
    font = _font_pdf(_render(jenis))
    assert font, "tak ada font terbaca dari PDF (alat ukur buta)"
    assert any(f.startswith("Inter") for f in font), font
    assert not any(f.startswith("DejaVu-Sans") and "Mono" not in f for f in font), font


def test_kontrol_alat_membaca_dejavu():
    """Kontrol merah alat: tanpa FontConfiguration, PDF yang sama memang DejaVu -- pembaca font bisa merah."""
    s = PS.get_pdf_service()
    r = s.render_receipt({"receipt_number": "RCV-T", "amount": 1, "amount_words": "x", "status": "posted"}, KOP)
    pdf = s.tulis_pdf(PS.Render(r.html, r.css, font=False))
    assert any(f.startswith("DejaVu") for f in _font_pdf(pdf))
