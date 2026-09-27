"""PDF faktur per status pembayaran (pemilik 27 Sep 2026; spek pdf-faktur-status). Template A asli dirender lewat
PDFService (HTML), konteks dari services/faktur_cetak. Aturan = literal spek, bukan konstanta modul."""
import re
from datetime import date, datetime, timezone
from decimal import Decimal as D
from zoneinfo import ZoneInfo

import pytest

from app.services import faktur_cetak as FC
from app.services.pdf_service import PDFService

CETAK = date(2026, 9, 27)


def _inv(status="posted", total=2970000, paid=0, due=None, **x):
    due = total - paid if due is None else due
    d = {"invoice_number": "INV-2609-0035", "invoice_date": "2026-09-13", "due_date": "2026-10-11",
         "customer_name": "Toko Merdeka", "status": status, "subtotal": total, "total_amount": total,
         "amount_paid": paid, "amount_due": due, "has_cents": False, "tax_amount": 0,
         "payment_bank_name": "BCA", "payment_account_number": "8295032185",
         "payment_account_holder": "BCA Anthonius Iwan Adhipraja", "tenant": {"name": "Grapgrap Clothing"},
         "items": [{"description": "Jaket", "product_name": "Jaket", "quantity": 11, "unit": "pcs",
                    "unit_price": 270000, "subtotal": 2970000, "total": 2970000}]}
    d.update(x)
    return d


DP = {"sumber": "uang_muka", "tanggal": date(2026, 9, 27), "nomor": "UM-2609-0012", "jenis": "Uang muka",
      "metode": "Transfer BCA", "jumlah": D("1500000")}
RP = {"sumber": "penerimaan", "tanggal": date(2026, 10, 3), "nomor": "PAY-2610-0004", "jenis": "Pembayaran",
      "metode": "Transfer BCA", "jumlah": D("1470000")}


def _html(inv, riwayat=(), cetak=CETAK):
    inv = dict(inv)
    inv["cetak"] = FC.keadaan_cetak(inv, cetak, list(riwayat), ZoneInfo("Asia/Makassar"))
    inv["rekening_pemilik_cetak"] = FC.pemilik_rekening(inv["payment_bank_name"], inv["payment_account_holder"])
    inv["catatan_transfer"] = f"Cantumkan nomor faktur {inv['invoice_number']} pada berita transfer."
    svc = PDFService()
    return svc.jinja_env.get_template("sales_invoice.html").render(**svc._konteks_faktur(inv))


def _hero(h):
    blok = re.search(r'<div class="hero hero-status">(.*?)<table', h, re.S).group(1)
    label = re.search(r'class="hero-label">([^<]*)<', blok).group(1).strip()
    angka = re.search(r'class="hero-amount[^"]*">([^<]*)<', blok).group(1).strip()
    return label, angka, blok


# ---------------------------------------------------------------- keadaan (fungsi murni)
@pytest.mark.parametrize("inv,riw,cetak,harap", [
    (_inv(), [], CETAK, "belum"),
    (_inv(), [], date(2026, 10, 16), "terlambat"),
    (_inv(paid=1500000), [DP], CETAK, "sebagian"),
    (_inv(paid=1500000), [DP], date(2026, 10, 12), "terlambat"),      # terlambat menang atas sebagian
    (_inv(paid=2970000), [DP, RP], date(2026, 12, 1), "lunas"),       # lunas menang atas lewat tempo
    (_inv(status="void"), [], CETAK, "batal"),
    (_inv(status="draft"), [], CETAK, "draf"),
])
def test_keadaan(inv, riw, cetak, harap):
    assert FC.keadaan_cetak(inv, cetak, riw)["jenis"] == harap


def test_hari_terlambat_dari_tanggal_cetak_bukan_hari_ini():
    k = FC.keadaan_cetak(_inv(), date(2026, 10, 16), [])
    assert k["hari_terlambat"] == 5
    k = FC.keadaan_cetak(_inv(), date(2026, 10, 11), [])
    assert k["jenis"] == "belum" and k["hari_lagi"] == 0


def test_pelunasan_dan_tanggal_lunas_dari_pembayaran_terakhir():
    k = FC.keadaan_cetak(_inv(paid=2970000), CETAK, [DP, RP])
    assert k["tanggal_lunas"] == date(2026, 10, 3) and k["metode_lunas"] == "Transfer BCA"
    assert [h["jenis"] for h in k["riwayat"]] == ["Uang muka", "Pelunasan"]
    assert k["selisih_riwayat"] == 0 and not k["tampil_rekening"]


def test_tanggal_batal_zona_tenant():
    inv = _inv(status="void", voided_at=datetime(2026, 9, 27, 17, 30, tzinfo=timezone.utc))
    assert FC.keadaan_cetak(inv, CETAK, [], ZoneInfo("Asia/Makassar"))["tanggal_batal"] == date(2026, 9, 28)


@pytest.mark.parametrize("bank,pemilik,harap", [
    ("BCA", "BCA Anthonius Iwan Adhipraja", "Anthonius Iwan Adhipraja"),
    ("Bank BCA", "BCA Operasional", "Operasional"),
    ("Bank BCA", "Bank BCA Operasional", "Operasional"),
    ("BCA", "Anthonius Iwan Adhipraja", "Anthonius Iwan Adhipraja"),
    ("BCA", "BCA", "BCA"),
    ("Mandiri", "BCA Budi", "BCA Budi"),
])
def test_pemilik_rekening_tanpa_duplikasi_bank(bank, pemilik, harap):
    assert FC.pemilik_rekening(bank, pemilik) == harap


@pytest.mark.parametrize("m,b,harap", [("bank_transfer", "Bank BCA", "Transfer BCA"), ("transfer", "BCA", "Transfer BCA"),
                                      ("cash", None, "Tunai"), ("transfer", None, "Transfer")])
def test_label_metode(m, b, harap):
    assert FC.label_metode(m, b) == harap


def test_sql_riwayat_cabang_ledger_dan_pagar():
    q = " ".join(FC.SQL_RIWAYAT.split())
    assert q.count("je.status = 'POSTED' AND je.reversed_by_id IS NULL") == 3
    assert "COALESCE(af.status, 'active') = 'active'" in q                       # alokasi dilepas tak tampil
    assert "cda.status = 'active'" in q and "je.source_type = 'DEPOSIT_APPLICATION'" in q   # uang muka ikut
    assert "cn.original_invoice_id = $2" in q
    assert "coa.account_type = 'RECEIVABLE' AND jl.credit > 0" in q
    assert q.count("tenant_id = $1") >= 6


# ---------------------------------------------------------------- template
def test_belum_dibayar():
    h = _html(_inv())
    assert _hero(h)[:2] == ("Sisa tagihan", "Rp 2.970.000")
    assert "14 hari lagi" in _hero(h)[2] and 'class="stempel' not in h
    assert "Rekening Pembayaran" in h and "RIWAYAT" not in h.upper().replace("RIWAYAT_", "")
    assert re.search(r"Dibayar</td>\s*<td class=\"value\">Rp 0<", h)


def test_terlambat_stempel_hari():
    h = _html(_inv(), cetak=date(2026, 10, 16))
    assert "terlambat 5 hari" in _hero(h)[2]
    assert re.search(r'stempel-terlambat">\s*<div class="stempel-teks">TERLAMBAT</div>\s*<div class="stempel-kecil">5 HARI', h)
    assert "Rekening Pembayaran" in h


def test_sebagian_riwayat_dp_dan_rekening():
    h = _html(_inv(paid=1500000), [DP])
    assert _hero(h)[:2] == ("Sisa tagihan", "Rp 1.470.000") and "sudah dibayar Rp 1.500.000" in _hero(h)[2]
    assert "Riwayat Pembayaran" in h and "UM-2609-0012" in h and "Uang muka &middot; Transfer BCA" in h
    assert "Rekening Pembayaran" in h and 'class="stempel' not in h


def test_lunas_total_bukan_nol_tanpa_rekening():
    h = _html(_inv(paid=2970000), [DP, RP])
    assert _hero(h)[:2] == ("Total", "Rp 2.970.000")
    assert "Dibayar lunas 3 Okt 2026 &middot; Transfer BCA" in _hero(h)[2]
    assert "Rekening Pembayaran" not in h and "berita transfer" not in h
    assert re.search(r'stempel-lunas">\s*<div class="stempel-teks">LUNAS</div>\s*<div class="stempel-kecil">3 OKT 2026', h)
    assert "Pelunasan &middot; Transfer BCA" in h and "PAY-2610-0004" in h


def test_batal_tanpa_rekening_tanpa_sisa_dengan_catatan():
    inv = _inv(status="void", paid=0, voided_at=datetime(2026, 9, 28, 3, 0, tzinfo=timezone.utc),
               voided_reason="pesanan diganti dengan INV-2609-0041")
    h = _html(inv)
    label, angka, blok = _hero(h)
    assert (label, angka) == ("Total", "Rp 2.970.000") and "hero-coret" in blok
    assert "Dibatalkan 28 Sep 2026 &middot; faktur ini tidak berlaku" in blok
    assert "Rekening Pembayaran" not in h and "Sisa tagihan" not in h and ">Dibayar<" not in h
    assert re.search(r"Status</td>\s*<td class=\"value\">Dibatalkan<", h)
    assert "Alasan: pesanan diganti dengan INV-2609-0041" in h
    assert "stempel-batal" in h and "28 SEP 2026" in h
    assert "DIBATALKAN</div>" not in h            # tanda diagonal lama tak menimpa stempel/tabel


def test_stempel_sekali_di_blok_angka_sebelum_tabel_dan_riwayat_rekening_sesudah_ringkasan():
    items = [dict(_inv()["items"][0], description=f"b{i}", product_name=f"b{i}") for i in range(30)]
    h = _html(_inv(paid=1500000, items=items), [DP], cetak=date(2026, 10, 16))
    assert h.count('class="stempel ') == 1
    i_stempel, i_tabel = h.index('class="stempel '), h.index("<table class=\"items-table")
    i_ring, i_riw, i_rek = h.index('class="summary"'), h.index("Riwayat Pembayaran"), h.index("Rekening Pembayaran")
    assert i_stempel < i_tabel < i_ring < i_riw < i_rek


def test_rekening_tanpa_duplikasi_bank_dan_baris_transfer():
    h = _html(_inv())
    assert "a.n. Anthonius Iwan Adhipraja" in h and "a.n. BCA" not in h
    assert "Cantumkan nomor faktur INV-2609-0035 pada berita transfer." in h


def test_kolom_tetap_dan_header_rata_kanan():
    h = _html(_inv())
    assert ".si-table .col-desc { width: 44%; }" in h and ".si-table .col-price { width: 19%; }" in h
    assert ".si-table thead th.col-price, .si-table thead th.col-amount { text-align: right; }" in h


def test_tanpa_pill_status_dan_dicetak_tanggal_tenant():
    h = _html(_inv(), cetak=date(2026, 10, 16))
    assert "status-pill" not in h and "status_label" not in h
    assert "Dicetak 16 Okt 2026" in h


def test_tanpa_keadaan_cetak_perilaku_lama():
    """Pemanggil lama (tanpa `cetak`) = template seperti sebelumnya (tanda diagonal batal tetap)."""
    svc = PDFService()
    inv = _inv(status="void", voided_reason="x")
    h = svc.jinja_env.get_template("sales_invoice.html").render(**svc._konteks_faktur(inv))
    assert "DIBATALKAN</div>" in h and 'class="stempel' not in h and 'class="hero"' in h


def test_css_stempel_mengalir_bukan_fixed():
    """position:fixed diulang WeasyPrint di SETIAP halaman; stempel wajib hanya halaman 1 (ikut alur blok angka)."""
    import os
    from app.services import pdf_service as P
    css = open(os.path.join(str(P.TEMPLATE_DIR), "invoice.css")).read()
    blok = re.search(r"\.stempel \{(.*?)\}", css, re.S).group(1)
    assert "position: absolute" in blok and "fixed" not in blok
    assert "transform: rotate(-8deg)" in blok and "opacity: .85" in blok
    assert re.search(r"\.hero-status \{ position: relative; \}", css)


PENANDA = '@page { @top-right { content: "DIBATALKAN";'


def test_batal_penanda_kecil_kepala_halaman_2_dst():
    h = _html(_inv(status="void", voided_reason="x"))
    assert PENANDA in h and "@page :first { @top-right { content: none; } }" in h


@pytest.mark.parametrize("inv", [_inv(), _inv(paid=2970000), _inv(paid=1500000), _inv(status="draft")])
def test_penanda_batal_tak_muncul_di_faktur_hidup(inv):
    assert "DIBATALKAN" not in _html(inv, [DP] if inv["amount_paid"] else [])
