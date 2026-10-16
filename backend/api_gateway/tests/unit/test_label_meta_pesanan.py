"""TEKS-PDF-ID (16 Okt 2026): label meta "Sales Order" di PDF Nota Kredit & Proforma -> "No. Pesanan" (MASTER).
Subjudul dwibahasa (Penawaran Harga / Quotation dst.), "Qty", dan templat B (varian Inggris pilihan 1 tenant) TETAP.
Render lewat jalur nyata; hanya label yang berubah, nomor pesanan tetap dicetak."""
import re

from app.services import pdf_service as PS
from app.services.pdf_service import get_pdf_service
from tests.unit.test_pdf_proforma_komposisi import _pf, tangkap  # noqa: F401
from tests.unit.test_u5b_dokumen_nota_kredit import TENANT, _data


def _label_meta(html):
    return re.findall(r'<td class="meta-label">([^<]*)</td>', html)


def test_proforma_label_meta_no_pesanan_nomor_tetap(tangkap):  # noqa: F811
    PS.get_pdf_service().generate_proforma_pdf(_pf(), {"name": "T"})
    html = tangkap["isi"]
    assert "No. Pesanan" in _label_meta(html) and "Sales Order" not in _label_meta(html)
    assert "SO-2609-0031" in html


def test_nota_kredit_label_meta_no_pesanan():
    html = get_pdf_service().render_credit_note(_data(), TENANT).html
    assert "Sales Order" not in _label_meta(html)
    assert "No. Pesanan" in _label_meta(html)


def test_proforma_kalimat_atas_pesanan_nomor_tetap(tangkap):  # noqa: F811
    PS.get_pdf_service().generate_proforma_pdf(_pf(), {"name": "T"})
    html = tangkap["isi"]
    assert "atas Sales Order" not in html
    assert re.search(r"atas Pesanan\s+SO-2609-0031", html)
