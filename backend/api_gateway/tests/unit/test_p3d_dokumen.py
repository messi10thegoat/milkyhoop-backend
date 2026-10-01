"""P3d SO-dokumen: kwitansi A5 melintang (putusan pemilik 1 Okt 2026), Rekap Pesanan, baris Rekap di faktur.

Ukuran kertas dibaca dari PDF SUNGGUHAN (MediaBox), bukan dari CSS. Rekap: angkanya diuji nyata di luar suite
(253 SO grapgrap+kaos: Σ pembayaran == sudah dibayar, sisa == Posisi P1); di sini tata letak per keadaan."""
import re
import zlib

import pytest

from app.services import pdf_service as PS

KOP = {"name": "Usaha", "address": None, "phone": None, "email": None, "logo_data": None}


def _isi_pdf(pdf: bytes) -> bytes:
    bagian = [pdf]
    for m in re.finditer(rb"stream\r?\n(.*?)\r?\nendstream", pdf, re.S):
        try:
            bagian.append(zlib.decompress(m.group(1)))
        except zlib.error:
            pass
    return b"\n".join(bagian)


def _mediabox(pdf: bytes):
    m = re.search(rb"/MediaBox\s*\[\s*0\s+0\s+([\d.]+)\s+([\d.]+)\s*\]", _isi_pdf(pdf))
    assert m, "MediaBox tak terbaca (alat buta)"
    return round(float(m.group(1))), round(float(m.group(2)))


def _kw(**x):
    d = {"receipt_number": "RCV-T", "receipt_date": "2026-10-01", "amount": 1500000,
         "amount_words": "Satu Juta Lima Ratus Ribu Rupiah", "payer_name": "Pelanggan", "status": "posted",
         "method": "Transfer Bank", "purpose_label": "Pelunasan Faktur", "purpose_ref": "INV-T"}
    d.update(x)
    return d


def test_kwitansi_a5_melintang_dari_pdf_sungguhan():
    assert _mediabox(PS.get_pdf_service().generate_receipt_pdf(_kw(), KOP)) == (595, 420)


def test_dokumen_lain_tetap_a4():
    pdf = PS.get_pdf_service().generate_quote_pdf({"quote_number": "Q", "status": "sent", "items": [], "total_amount": 0}, KOP)
    assert _mediabox(pdf) == (595, 842)


@pytest.mark.parametrize("x,stempel", [
    ({}, True),                                                   # penerimaan uang sungguhan
    ({"hide_method": True, "title": "Bukti Penerapan Uang Muka"}, False),  # varian jurnal: tak ada uang baru
    ({"status": "void", "voided_at": "2026-10-01T01:00:00Z"}, False),       # batal: tanda DIBATALKAN, bukan DITERIMA
    ({"status": "draft"}, False),
])
def test_stempel_diterima_hanya_untuk_uang_yang_benar_diterima(x, stempel):
    r = PS.get_pdf_service().render_receipt(_kw(**x), KOP)
    assert ("kw-stempel-diterima" in r.html) is stempel
    assert "Satu Juta Lima Ratus Ribu Rupiah" in r.html and "INV-T" in r.html


def _rekap(**x):
    d = {"order_number": "SO-T", "order_date": "2026-10-01", "customer_name": "Pelanggan", "status": "terbuka",
         "total": 1000, "dibayar": 0, "nota_kredit": 0, "sisa": 1000, "tagihan": [], "pembayaran": []}
    d.update(x)
    return " ".join(PS.get_pdf_service().render_rekap_pesanan(d, KOP).html.split())


def test_rekap_terbuka_hero_sisa_tanpa_stempel_dan_rp0_tanpa_minus():
    h = _rekap()
    assert "Sisa tagihan</div> <div class=\"hero-amount\">Rp 1.000" in h
    assert "stempel-lunas" not in h and "stempel-batal" not in h
    assert "-Rp 0" not in h


def test_rekap_lunas_dan_batal_berstempel():
    assert "stempel-lunas" in _rekap(status="lunas", dibayar=1000, sisa=0)
    h = _rekap(status="batal", sisa=0)
    assert "stempel-batal" in h and "Rekening Pembayaran" not in h


def test_faktur_ber_so_menunjuk_rekap(monkeypatch):
    tangkap = {}

    class _HTML:
        def __init__(self, string=None, **kw):
            tangkap["isi"] = string

        def write_pdf(self, **kw):
            return b"%PDF"
    monkeypatch.setattr(PS, "HTML", _HTML)
    inv = {"invoice_number": "INV-T", "status": "posted", "items": [], "total_amount": 0,
           "sales_order_number": "SO-T", "rekap_pembayaran": 3}
    PS.get_pdf_service().generate_sales_invoice_pdf(inv)
    assert "Rincian 3 pembayaran ada di Rekap Pesanan SO-T." in tangkap["isi"]
    PS.get_pdf_service().generate_sales_invoice_pdf(dict(inv, rekap_pembayaran=0))
    assert "Rekap Pesanan" not in tangkap["isi"]
    PS.get_pdf_service().generate_sales_invoice_pdf(dict(inv, status="void", voided_at="2026-10-01T01:00:00Z",
                                                         cetak={"jenis": "batal", "riwayat": []}))
    assert "Rekap Pesanan" not in tangkap["isi"]  # faktur batal: tak menunjuk ke rekap


def test_surat_jalan_berlogo_kanan_atas():
    """MASTER 1 Okt: SJ satu-satunya dokumen tanpa logo (03-DOKUMEN: logo kanan atas semua dokumen)."""
    s = PS.get_pdf_service()
    r = s.render_delivery_note({"delivery_number": "SJ-T", "items": [], "tenant": dict(KOP, logo_data="data:image/png;base64,AAAA")})
    assert '<img src="data:image/png;base64,AAAA" class="doc-logo"' in r.html
    r = s.render_delivery_note({"delivery_number": "SJ-T", "items": [], "tenant": KOP})
    assert "doc-logo\"" not in r.html.split("</style>")[-1]


def test_proforma_jarak_dirapatkan_untuk_muat_satu_halaman():
    """PRO-2610-0001 PELUNASAN + logo dulu 2 halaman (diukur: 49/112 proforma grapgrap 2 halaman -> 2)."""
    src = (PS.TEMPLATE_DIR / "proforma.html").read_text()
    for aturan in (".header { margin-bottom: 18px !important; }", ".company-logo { max-height: 60px !important; }",
                   ".meta-table { border-spacing: 20px 3px !important; }"):
        assert aturan in src, aturan
