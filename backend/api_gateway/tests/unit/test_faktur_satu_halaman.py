"""Faktur templat A muat 1 halaman (1 Okt 2026, MASTER/pemilik: pemilik mengirim faktur ke pelanggan).
Diukur 107 faktur nyata grapgrap+kaos sebelum/sesudah: 2 halaman 16 -> 0 (grapgrap 15/41 -> 0), halaman NAIK 0,
isi teks (multiset huruf) identik 107/107. Pola sama dengan proforma (test_p3d_dokumen)."""
from app.services import pdf_service as PS


def test_faktur_a_dicetak_ke_margin_dan_jarak_dirapatkan():
    src = (PS.TEMPLATE_DIR / "sales_invoice.html").read_text()
    kepala = src[:src.index("</head>")]
    for aturan in (".footer-line { position: running(dicetak);",
                   "@page { margin-bottom: 1.4cm !important; @bottom-left { content: element(dicetak); } }",
                   ".header { margin-bottom: 18px !important; }", ".company-logo { max-height: 60px !important; }",
                   ".meta-table { border-spacing: 20px 3px !important; }"):
        assert aturan in kepala, aturan


def test_faktur_b_tak_disentuh():
    assert "running(dicetak)" not in (PS.TEMPLATE_DIR / "sales_invoice_b.html").read_text()


def test_faktur_30_baris_tetap_multi_halaman_dan_bernomor():
    """Multi-halaman tetap: 30 baris -> >= 2 halaman; nomor 'Halaman i dari N' tetap di @bottom-right invoice.css (teks
    PDF per halaman diperiksa di luar suite: 30 baris -> 'Halaman 1/2/3 dari 3')."""
    s = PS.get_pdf_service()
    baris = [{"product_name": f"Kaos {i}", "qty": 1, "price": 100000, "subtotal": 100000} for i in range(30)]
    data = {"invoice_number": "INV-T", "invoice_date": "2026-10-01", "due_date": "2026-10-08", "items": baris,
            "tenant": {"name": "Usaha"}, "customer": {"name": "Pelanggan"}, "subtotal": 3000000,
            "total_amount": 3000000, "amount_paid": 0, "amount_due": 3000000}
    assert len(s.dokumen(s.render_sales_invoice(data, "a")).pages) >= 2
    css = (PS.TEMPLATE_DIR / "invoice.css").read_text()
    assert 'content: "Halaman " counter(page) " dari " counter(pages);' in css
    assert "@bottom-right" not in (PS.TEMPLATE_DIR / "sales_invoice.html").read_text()
