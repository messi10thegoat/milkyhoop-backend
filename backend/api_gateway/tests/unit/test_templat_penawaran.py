"""Templat PDF Penawaran BARU (7 Okt 2026; SPEC pemilik "template-penawaran" + putusan pemilik). Struktur & aturan tampil."""
import pathlib
import re
from decimal import Decimal

from app.services import penawaran_cetak as PC
from app.services import pdf_service as PSV

DASAR = {"quote_number": "QUO-1", "quote_date": "2026-10-06", "expiry_date": "2026-10-20", "status": "sent",
         "customer_name": "PT Contoh", "subtotal": 1500000, "total_amount": 1500000, "has_cents": False,
         "items": [{"description": "Kaos", "quantity": 3, "unit": "pcs", "unit_price": 500000, "line_total": 1500000}],
         "payment_bank_name": "BCA", "payment_account_number": "8295032185", "dp_amount": 500000, "dp_percent": 33}


def _html(**q):
    d = dict(DASAR); d.update(q)
    return PSV.get_pdf_service().render_quote(d, {"name": "Grapgrap Clothing", "address": "Jl. X", "phone": "081243065632"})


def test_format_telepon():
    assert PC.format_telepon("081243065632") == "0812 4306 5632"
    assert PC.format_telepon("+62 812-4306-5632") == "0812 4306 5632"
    assert PC.format_telepon("0431-123") == "0431-123" and PC.format_telepon(None) is None


def test_up_hanya_bila_beda_dari_nama_pelanggan():
    assert PC.baris_up("Alwan Rikun", " alwan  rikun ", None) is None
    assert PC.baris_up("Komisi Pemuda", "Ibu Grace", "Ketua") == "Up. Ibu Grace, Ketua"
    assert PC.baris_up("X", None, "Ketua") is None


def test_kontak_bagian_kosong_dilewati_dan_label_pajak():
    assert PC.bagian_kontak("Anton", "081243065632", "a@x.id") == [("teks", "Anton"), ("teks", "0812 4306 5632"), ("email", "a@x.id")]
    assert PC.bagian_kontak(None, "", "a@x.id") == [("email", "a@x.id")] and PC.bagian_kontak(None, None, None) == []
    assert PC.label_pajak([{"tax_rate": 11}, {"tax_rate": Decimal("11.00")}, {"tax_rate": 0}]) == "PPN 11%"
    assert PC.label_pajak([{"tax_rate": 11}, {"tax_rate": 12}]) == "PPN"


def test_tidak_ada_yang_dilarang_spec():
    h = _html().html
    assert "8295032185" not in h and "Rekening" not in h          # rekening
    assert "hero" not in h                                         # angka total besar
    assert h.count("Berlaku s/d") == 1                             # tidak dobel
    assert "Uang Muka" not in h and "Sisa Pembayaran" not in h     # hitungan DP
    assert "Terbilang:" not in h and "Quotation" in h


def test_up_kontak_dan_ttd_sesuai_putusan_pemilik():
    h = _html(attention_name="PT Contoh", signer_name="Anton", signer_title="Direktur", signer_phone="081243065632",
              signer_email="a@x.id", total_in_words="Satu Juta Rupiah").html
    assert "Up. " not in h                                         # sama dengan nama pelanggan
    assert "<b>Kontak</b><span>Anton · 0812 4306 5632 · <!--email_off-->a@x.id<!--/email_off--></span>" in h
    ttd = h[h.index('class="sign"'):]
    assert "0812" not in ttd and "a@x.id" not in ttd                # blok ttd TANPA kontak
    assert "<b>Anton</b>" in ttd and "Direktur" in ttd and "Hormat kami," in ttd
    assert '<div class="terb">Satu Juta Rupiah</div>' in h


def test_kosong_tidak_dicetak_tanpa_teks_bawaan():
    h = _html().html
    for kata in ('class="intro', "Catatan khusus", "Ketentuan", 'class="closing', "<b>Kontak</b>", "Dengan hormat"):
        assert kata not in h, kata
    h2 = _html(opening_text="Dengan hormat,\nBaris", notes="N", terms="T1\nT2", closing_text="Terima kasih").html
    assert h2.index("Catatan khusus") < h2.index("Ketentuan") < h2.index('class="closing') < h2.index('class="sign"')
    assert 'class="intro pre">Dengan hormat,\nBaris' in h2 and 'class="mut pre">T1\nT2' in h2


def test_draf_hanya_draft_dan_css_khusus_penawaran():
    assert "DRAF" in _html(status="draft").html and "DRAF" not in _html(status="sent").html
    r = _html()
    assert r.css == PSV.CSS_PENAWARAN and "invoice.css" not in r.css
    css = (pathlib.Path(PSV.__file__).resolve().parents[1] / "templates" / "pdf" / "penawaran.css").read_text()
    for v in ("margin: 70px 76px", "#63635E", "#8E8E89", "#EFEDE8", "1.2px solid #1A1A1A", "width: 42%", "height: 40px",
              'counter(page) " dari " counter(pages)', "break-inside: avoid", "table-header-group"):
        assert v in css, v


def test_ringkasan_diskon_ppn_hanya_bila_ada():
    h = _html().html
    assert "Diskon" not in h and "PPN" not in h
    h2 = _html(discount_amount=100000, discount_type="percentage", discount_value=10, tax_amount=154000,
               items=[dict(DASAR["items"][0], tax_rate=11)]).html
    assert "Diskon 10%" in h2 and "PPN 11%" in h2


def test_semua_email_cetak_terbungkus_email_off():
    app = pathlib.Path(PSV.__file__).resolve().parents[1] / "templates" / "pdf"
    telanjang = [(f.name, m.group(0)) for f in app.rglob("*.html") for m in re.finditer(r"\{\{ [a-z_.]*email \}\}", f.read_text())
                 if "<!--email_off-->" + m.group(0) not in f.read_text()]
    assert not telanjang, telanjang
