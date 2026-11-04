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
    return round(hal.margin_top, 1), round(hal.margin_right, 1), round(hal.margin_bottom, 1), round(hal.margin_left, 1), round(hal.width, 1)


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
def test_margin_halaman_sama_semua_dokumen(nama):
    """Margin = pola SI (1.1cm 1.25cm 1.4cm 1.25cm = 41.6/47.2/52.9/47.2 px) -- diukur dari tata letak."""
    assert _kotak(_render(nama))[:4] == (41.6, 47.2, 52.9, 47.2)


def test_kwitansi_setengah_a4_margin_sendiri():
    assert _kotak(_render("kwitansi"))[:4] != (41.6, 47.2, 52.9, 47.2)


def test_tak_ada_margin_halaman_di_luar_token():
    """Nilai HANYA di dokumen.css (+ pengecualian kwitansi.css / invoice_b.css): templat & CSS keluarga tak menimpa lagi."""
    for p in sorted(TPL.glob("*.html")) + sorted(TPL.glob("*.css")) + sorted((TPL / "_partials").glob("*.html")):
        if p.name in ("dokumen.css", "kwitansi.css", "invoice_b.css", "sales_invoice_b.html", "report.css") or p.name.startswith(("laba_rugi", "neraca", "arus_kas")):
            continue
        s = p.read_text()
        for blok in re.findall(r"@page\s*\{(?:[^{}]|\{[^{}]*\})*\}", s):
            assert not re.search(r"(?<![-\w])margin(-(top|right|bottom|left))?\s*:", re.sub(r"@[\w-]+\s*\{[^{}]*\}", "", blok)), (p.name, blok)


def test_ukuran_logo_hanya_token():
    """Logo SATU sumber (putusan pemilik 4 Nov 2026): di luar dokumen.css tak ada aturan logo ber-ukuran (angka ATAU token) dan tak ada
    token --logo-* lain; <img> logo di semua templat = class="logo" lewat logo_rapat (SI templat B pilihan tenant dikecualikan)."""
    lain = ("invoice_b.css", "sales_invoice_b.html")
    for p in sorted(TPL.glob("*.html")) + sorted(TPL.glob("*.css")) + sorted((TPL / "_partials").glob("*.html")):
        if p.name in lain or p.name == "dokumen.css":
            continue
        s = p.read_text()
        assert not re.search(r"\.(logo|company-logo|doc-logo|kw-logo)(?![-\w])[^{};]*\{[^}]*(?<![-\w])(width|height|max-height|max-width)\s*:", s), p.name
        assert "--logo-" not in s, p.name
        for img in re.findall(r"<img[^>]*>", s):
            if "logo" in img:
                assert 'class="logo"' in img and "| logo_rapat" in img, (p.name, img)
    d = (TPL / "dokumen.css").read_text()
    assert re.findall(r"--logo-[\w-]+(?=\s*:)", d) == ["--logo-tinggi", "--logo-lebar"], d


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


def _logo_berpadding():
    from PIL import Image, ImageDraw
    im = Image.new("RGB", (295, 295), "white"); ImageDraw.Draw(im).rectangle([16, 88, 279, 207], fill="black")
    b = io.BytesIO(); im.save(b, "PNG"); return "data:image/png;base64," + base64.b64encode(b.getvalue()).decode()


def _kotak_logo(r):
    fc = FontConfiguration()
    d = HTML(string=r.html).render(stylesheets=PS.get_pdf_service()._lembar_gaya(r, fc), font_config=fc)

    def cari(b):
        for c in getattr(b, "children", None) or []:
            if getattr(c, "element_tag", None) == "img":
                return c
            x = cari(c)
            if x:
                return x
    img = cari(d.pages[0]._page_box)
    return (round(img.width, 1), round(img.height, 1))


def test_kotak_logo_semua_dokumen_sama_untuk_logo_sama():
    """Logo ber-padding (grapgrap) -> kotak logo SAMA di SEMUA dokumen (putusan pemilik 4 Nov 2026: satu token = SI live - 20%)."""
    kotak = {n: _kotak_logo(x) for n, x in _semua_dokumen(_logo_berpadding()).items()}
    # isi terpangkas 264x120 -> muat 144 x 76,8 (= 180 x 96 SI live dikurangi 20%) -> 144 x 65,5
    assert set(kotak.values()) == {(144.0, 65.5)}, kotak
    for f in ("sales_invoice.html", "credit_note.html", "proforma.html", "rekap_pesanan.html"):   # kepala SATU partial, tanpa salinan <img>
        t = (TPL / f).read_text()
        assert '{% from "_partials/kepala_si.html" import kepala %}' in t and 'class="company-logo"' not in t, f


def _semua_dokumen(lg=None, npwp=None):
    """Satu render per dokumen usaha (logo, tanpa baris) -- dipakai tes footer & HTML layar."""
    s, t = PS.get_pdf_service(), {"name": "U", "address": "Jl. Uji 1", "phone": "0812", "npwp": npwp, "logo_data": lg or _logo()}
    return {
        "si": s.render_sales_invoice({"invoice_number": "INV-1", "status": "posted", "tenant": t, "customer": {"name": "A"}, "items": [],
                                      "subtotal": 1, "total_amount": 1, "amount_due": 1}),
        "nk": s.render_credit_note({"credit_note_number": "CN-1", "status": "posted", "items": [], "total_amount": 1}, t),
        "proforma": s.render_proforma({"proforma_number": "PRO-1", "purpose": "DP", "amount": 1, "order_items": [], "status": "posted"}, t),
        "rekap": s.render_rekap_pesanan({"order_number": "SO-1", "status": "sebagian", "total": 1, "dibayar": 0, "sisa": 1, "tagihan": [],
                                         "pembayaran": []}, t),
        "penawaran": s.render_quote({"quote_number": "Q-1", "status": "sent", "items": [], "total_amount": 1}, t),
        "po": s.render_purchase_order({"po_number": "PO-1", "status": "sent", "vendor_name": "V", "items": [], "subtotal": 1, "total_amount": 1}, t),
        "fpb": s.render_bill({"invoice_number": "PB-1", "status": "posted", "tenant": t, "vendor": {"name": "V"}, "items": [], "subtotal": "1",
                              "grand_total": "1", "amount_paid": "0", "amount_due": "1"}),
        "nd": s.render_debit_note({"debit_note_number": "DN-1", "status": "posted", "items": [], "total_amount": 1}, t),
        "sj": s.render_delivery_note({"delivery_number": "SJ-1", "tenant": t, "customer_name": "A", "items": []}),
        "grn": s.render_goods_receipt({"receipt_number": "GRN-1", "status": "posted", "items": []}, t),
        "kwitansi": s.render_receipt({"receipt_number": "RCV-1", "amount": 1, "status": "posted"}, t),
    }


@pytest.mark.parametrize("nama", ["si", "nk", "proforma", "rekap", "penawaran", "po", "fpb", "nd", "sj", "grn", "kwitansi"])
def test_dicetak_di_kaki_halaman_pdf_dan_layar(nama):
    """"Dicetak" (temuan MASTER 4 Nov 2026, panel PO-2611-0011: tampil DI ATAS judul). PDF: teksnya di kotak margin @bottom-left, nol di isi.
    HTML panel (html_layar, browser tanpa position: running()): elemennya ditaruh absolut di kaki kertas oleh @media screen dokumen.css."""
    r = _semua_dokumen()[nama]
    fc = FontConfiguration()
    hal = HTML(string=r.html).render(stylesheets=PS.get_pdf_service()._lembar_gaya(r, fc), font_config=fc).pages[0]._page_box

    def teks(b):
        return (getattr(b, "text", None) or "") + "".join(teks(c) for c in getattr(b, "children", None) or [])
    kotak = {getattr(c, "at_keyword", None): teks(c) for c in hal.children}
    assert "Dicetak" in kotak.get("@bottom-left", ""), kotak.keys()
    assert "Dicetak" not in kotak[None], nama
    layar = PS.get_pdf_service().html_layar(r)
    kelas = re.search(r'<div class="(footer-line|kaki-kiri)">\s*Dicetak', layar).group(1)
    aturan = re.search(r"@media screen \{\s*body \{ position: relative; \}\s*([^{]+)\{([^}]*)\}", layar)
    assert aturan and kelas in aturan.group(1) and "position: absolute" in aturan.group(2) and "bottom:" in aturan.group(2), nama
    assert re.search(r"(?<![\w.-])th \{ text-align: left; \}", layar), nama   # C: kepala kolom rata kiri juga di browser (INV-2609-0011)


def test_identitas_usaha_satu_sumber_npwp_bila_ada():
    """Putusan MASTER 4 Nov 2026 (pola Accurate/NetSuite): NPWP usaha DICETAK di kop/"Dari" SEMUA dokumen bila tenant punya tax_id;
    tanpa tax_id tak ada baris. Satu partial _partials/identitas_usaha (kop_usaha Pembelian + blok Dari Penjualan + kwitansi)."""
    ada, tanpa = _semua_dokumen(npwp="01.234.567.8-901.000"), _semua_dokumen()
    for n in ada:
        assert ada[n].html.count("NPWP 01.234.567.8-901.000") == 1, n
        assert "NPWP" not in re.sub(r"<!--.*?-->", "", tanpa[n].html, flags=re.S), n
    for p in sorted(TPL.glob("*.html")) + sorted((TPL / "_partials").glob("*.html")):
        if p.name in ("identitas_usaha.html", "sales_invoice_b.html") or p.name.startswith(("laba_rugi", "neraca", "arus_kas")):
            continue
        t = p.read_text()   # tak ada salinan baris identitas usaha di luar partial
        assert not re.search(r"\b(company|tenant|kop|t)\.(address|phone|npwp)\b", t), p.name


def test_kop_tenant_satu_pemuat_satu_pembentuk():
    """Kop "Tenant" dibaca HANYA lewat rekap_pesanan.muat_kop (dulu inline di 5 router; 4 tanpa tax_id) dan `company` templat dibentuk
    HANYA oleh PDFService._usaha (dulu 7 salinan dict)."""
    import pathlib
    app = pathlib.Path(PS.__file__).parent.parent
    for f in sorted((app / "routers").glob("*.py")) + sorted((app / "services").glob("*.py")):
        t = f.read_text()
        if f.name not in ("rekap_pesanan.py", "auth.py", "user.py", "akun_saya.py", "tenant_profile.py"):   # 4 terakhir: profil/sesi, bukan PDF
            assert '"static" / "logos"' not in t and not re.search(r'logo_url[^\n]*FROM "Tenant"', t), f.name
    src = (app / "services" / "pdf_service.py").read_text()
    assert src.count('tenant_info.get("logo_data")') == 1 and src.count('"logo_base64":') == 1   # satu-satunya dict company = _usaha


@pytest.mark.parametrize("npwp", [None, "", "01.234.567.8-901.000"])
def test_si_templat_b_baris_tax_hanya_bila_npwp_pelanggan(npwp):
    """Putusan pemilik 4 Nov 2026: baris "TAX : <NPWP pelanggan>" templat B hanya bila NPWP terisi (dulu "TAX : " kosong selalu)."""
    inv = {"invoice_number": "INV-1", "status": "posted", "tenant": {"name": "U", "is_pkp": False}, "customer_name": "A", "customer_npwp": npwp,
           "items": [], "subtotal": 0, "total_amount": 0, "amount_due": 0}
    h = PS.get_pdf_service().render_sales_invoice(inv, template="b").html
    assert h.count("TAX :") == (1 if npwp else 0) and (not npwp or f"TAX : {npwp}" in h), npwp


def test_dokumen_gudang_stempel_batal_sj_dan_grn():
    """Templat gudang SATU (SJ + GRN): stempel DIBATALKAN dari _tanda_batal (dulu hanya goods_receipt.html lama; SJ tak pernah)."""
    s = PS.get_pdf_service()
    for hidup, batal in ((s.render_delivery_note({"delivery_number": "SJ-1", "status": "posted", "items": []}),
                          s.render_delivery_note({"delivery_number": "SJ-1", "status": "voided", "voided_reason": "Salah PO", "items": []})),
                         (s.render_goods_receipt({"receipt_number": "GRN-1", "status": "posted", "items": []}, {}),
                          s.render_goods_receipt({"receipt_number": "GRN-1", "status": "void", "voided_reason": "Salah PO", "items": []}, {}))):
        assert "DIBATALKAN" not in hidup.html and "DIBATALKAN" in batal.html and "Salah PO" in batal.html
