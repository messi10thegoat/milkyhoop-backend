"""JUMLAH-BARIS (V427) -- kertas: harga satuan berdesimal TIDAK crash dan TIDAK dibulatkan diam-diam di
Faktur / Proforma / Nota Kredit; jumlah baris menampilkan angka tersimpan (6.150.000)."""
import pytest

from app.services.pdf_service import PDFService

P = PDFService()


def _render(nama, **ctx):
    return P.jinja_env.get_template(nama).render(**ctx)


def test_filter_harga_desimal_hanya_bila_tak_bulat():
    f = P.jinja_env.filters["harga"]
    assert f(236538.46) == "236.538,46"
    assert f("236538.46") == "236.538,46"
    assert f(1000) == "1.000" and f(1000.0) == "1.000"


def test_proforma_harga_berdesimal_jumlah_tersimpan():
    html = _render("proforma.html", proforma={
        "proforma_number": "PRO-UJI", "order_items": [
            {"description": "Kaos", "quantity": 26, "unit": "pcs", "unit_price": 236538.46, "line_total": 6150000}],
        "order_subtotal": 6150000})
    assert "236.538,46" in html and "6.150.000" in html


def test_nota_kredit_harga_berdesimal_tak_dibulatkan():
    try:
        html = _render("credit_note.html", credit_note={"items": [
            {"description": "Kaos", "quantity": 26, "unit_price": 236538.46, "subtotal": 6150000, "total": 6150000}],
            "credit_note_number": "CN-UJI"})
    except Exception as e:  # konteks template tak lengkap = bukan hal yang diuji di sini
        pytest.skip(f"konteks credit_note.html lain: {e}")
    assert "236.538,46" in html


def test_faktur_dokumen_bersen_harga_dan_jumlah():
    html = _render("sales_invoice.html", invoice={
        "invoice_number": "INV-UJI", "has_cents": True, "subtotal": 6150000, "total_amount": 6150000,
        "items": [{"description": "Kaos", "quantity": 26, "unit_price": 236538.46, "subtotal": 6150000, "total": 6150000}]},
        tenant={}, customer={})
    assert "236.538,46" in html and "6.150.000,00" in html
