"""PDF SERAGAM tahap 1 (4 Nov 2026, permintaan pemilik via MASTER): token cetak SATU sumber templates/pdf/dokumen.css -- margin halaman
pola Faktur Penjualan, logo pola Faktur Pembelian -- dimuat TERAKHIR oleh semua dokumen; kwitansi (setengah A4) & SI templat B
dikecualikan; tabel barang Penawaran/PO/FPB = SATU makro. Tata letak diukur dari HASIL WeasyPrint (kotak halaman), bukan teks CSS."""
import base64
import io
import re

import pytest
from weasyprint import HTML
from weasyprint.text.fonts import FontConfiguration

from app.services import pdf_service as PS

TPL = PS.TEMPLATE_DIR
LOGO = None


def _logo():
    global LOGO
    if LOGO is None:
        from PIL import Image
        b = io.BytesIO(); Image.new("RGB", (600, 150), "white").save(b, "PNG")
        LOGO = "data:image/png;base64," + base64.b64encode(b.getvalue()).decode()
    return LOGO


def _kotak(r):
    fc = FontConfiguration()
    d = HTML(string=r.html).render(stylesheets=PS.get_pdf_service()._lembar_gaya(r, fc), font_config=fc)
    hal = d.pages[0]._page_box

    def logo(b):
        for c in getattr(b, "children", None) or []:
            if getattr(c, "element_tag", None) == "img":
                return c
            x = logo(c)
            if x:
                return x
    img = logo(hal)
    return (round(hal.margin_top, 1), round(hal.margin_right, 1), round(hal.margin_bottom, 1), round(hal.margin_left, 1),
            round(hal.width, 1)), (img.height if img else None)


def _render(nama):
    s, t = PS.get_pdf_service(), {"name": "Usaha Uji", "address": "Jl. Uji 1", "phone": "0812", "logo_data": _logo()}
    item = {"description": "Barang", "product_name": "Barang", "quantity": 2, "qty": 2.0, "unit": "pcs", "unit_price": 1000, "price": "1000.00",
            "line_total": 2000, "total": "2000.00", "subtotal": 2000}
    if nama == "si":
        return s.render_sales_invoice({"invoice_number": "INV-1", "status": "posted", "tenant": t, "customer": {"name": "A"}, "items": [item],
                                       "subtotal": 2000, "total_amount": 2000, "amount_due": 2000, "has_cents": False})
    if nama == "fpb":
        return s.render_bill({"invoice_number": "PB-1", "status": "posted", "tenant": t, "vendor": {"name": "V"}, "items": [item],
                              "subtotal": "2000", "grand_total": "2000", "amount_paid": "0", "amount_due": "2000", "has_cents": False})
    if nama == "po":
        return s.render_purchase_order({"po_number": "PO-1", "status": "sent", "vendor_name": "V", "items": [item], "subtotal": 2000,
                                        "total_amount": 2000, "has_cents": False}, t)
    if nama == "sj":
        return s.render_delivery_note({"delivery_number": "SJ-1", "tenant": t, "customer_name": "A", "items": [item]})
    if nama == "kwitansi":
        return s.render_receipt({"receipt_number": "RCV-1", "amount": 1, "amount_words": "x", "status": "posted"},
                                {**t, "logo_base64": _logo()})
    raise AssertionError(nama)


def test_semua_dokumen_memuat_token_terakhir_kwitansi_sesudahnya():
    assert PS.CSS_DOKUMEN[-1] == "dokumen.css" and PS.CSS_PENAWARAN[-1] == "dokumen.css"
    assert PS.CSS_KWITANSI[-2:] == ("dokumen.css", "kwitansi.css")   # @page setengah A4 kwitansi menang (dikecualikan)


@pytest.mark.parametrize("nama", ["si", "fpb", "po", "sj"])
def test_margin_halaman_dan_logo_sama_semua_dokumen(nama):
    """Margin = pola SI (1.1cm 1.25cm 1.4cm 1.25cm = 41.6/47.2/52.9/47.2 px); logo = pola FPB 96 px -- diukur dari tata letak."""
    margin, logo = _kotak(_render(nama))
    assert margin[:4] == (41.6, 47.2, 52.9, 47.2), margin
    # kop pola Accurate (FPB 4 Nov 2026; PO, SJ turunannya): kotak logo = gambar (maks token) agar nama usaha rapat; logo uji 600x150 -> 45 px
    assert logo == (45 if nama in ("fpb", "po", "sj") else 96), logo


def test_kwitansi_setengah_a4_tetap_logo_token_kecil():
    margin, logo = _kotak(_render("kwitansi"))
    assert margin[:4] != (41.6, 47.2, 52.9, 47.2) and logo <= 64, (margin, logo)
    assert "var(--logo-kecil-tinggi)" in (TPL / "kwitansi.css").read_text()


def test_tak_ada_margin_halaman_atau_ukuran_logo_di_luar_token():
    """Nilai HANYA di dokumen.css (+ pengecualian kwitansi.css / invoice_b.css): templat & CSS keluarga tak menimpa lagi."""
    for p in sorted(TPL.glob("*.html")) + sorted(TPL.glob("*.css")) + sorted((TPL / "_partials").glob("*.html")):
        if p.name in ("dokumen.css", "kwitansi.css", "invoice_b.css", "sales_invoice_b.html", "report.css") or p.name.startswith(("laba_rugi", "neraca", "arus_kas")):
            continue
        s = p.read_text()
        for blok in re.findall(r"@page\s*\{(?:[^{}]|\{[^{}]*\})*\}", s):
            assert not re.search(r"(?<![-\w])margin(-(top|right|bottom|left))?\s*:", re.sub(r"@[\w-]+\s*\{[^{}]*\}", "", blok)), (p.name, blok)
        assert not re.search(r"(company-logo|\.logo|doc-logo)\s*\{[^}]*(height|max-height|max-width)\s*:\s*\d", s), p.name


def test_tabel_barang_satu_makro_tiga_dokumen():
    for f in ("quote.html", "purchase_order.html", "bill_invoice.html"):
        s = (TPL / f).read_text()
        assert '{% from "_partials/tabel_barang.html" import tabel_barang %}' in s and s.count("tabel_barang(") == 1, f
        assert "<th>Deskripsi</th>" not in s, f   # tabel lama dihapus, bukan ditimpa (kepala tabel hanya di makro)
    m = (TPL / "_partials" / "tabel_barang.html").read_text()
    assert "{{ item[k.qty] | qty }}" in m and '<th class="r">Diskon</th>' in m and "{% else %}-{% endif %}" in m


def test_logo_rapat_pangkas_pinggiran_polos():
    """Kop Pembelian (FPB nyata grapgrap 4 Nov 2026): PNG 295x295 berisi wordmark di baris 88-208 -> dipangkas ke isi; logo tanpa
    pinggiran / bukan data URI / rusak = apa adanya."""
    from PIL import Image, ImageDraw
    im = Image.new("RGB", (295, 295), "white"); ImageDraw.Draw(im).rectangle([16, 88, 279, 207], fill="black")
    b = io.BytesIO(); im.save(b, "PNG"); uri = "data:image/png;base64," + base64.b64encode(b.getvalue()).decode()
    hasil = PS.logo_rapat(uri)
    assert Image.open(io.BytesIO(base64.b64decode(hasil.split(",", 1)[1]))).size == (264, 120)
    penuh = _logo()   # putih polos seluruhnya = tak ada isi -> apa adanya
    assert PS.logo_rapat(penuh) == penuh and PS.logo_rapat(None) is None and PS.logo_rapat("data:image/png;base64,rusak") == "data:image/png;base64,rusak"
    assert "{{ logo | logo_rapat }}" in (TPL / "_partials" / "kop_usaha.html").read_text()
    # cetak massal: hasil pangkas di-cache per proses (kunci = data URI = isi gambar), PIL tak dijalankan ulang
    sebelum = PS.logo_rapat.cache_info().hits
    assert PS.logo_rapat(uri) is hasil and PS.logo_rapat.cache_info().hits == sebelum + 1
