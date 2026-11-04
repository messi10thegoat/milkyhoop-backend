"""Ringkasan angka dokumen SATU sumber (services/ringkasan_cetak, PDF seragam 4 Nov 2026, putusan MASTER pola Accurate/QBO/Xero):
Subtotal = SIGMA kolom Total baris (neto), tanpa "Diskon item"; angka akhir (DPP/PPN/Total/Dibayar/Sisa) = kolom tersimpan, identik."""
import re
from decimal import Decimal as D

import pytest
from weasyprint import HTML
from weasyprint.text.fonts import FontConfiguration

from app.services import pdf_service as PS
from app.services import ringkasan_cetak as RC


def _nilai(rows, label):
    return next(r["nilai"] for r in rows if r["label"] == label)


def _si():
    items = [{"product_name": "Minyak 2L", "quantity": 12, "unit": "dus", "unit_price": D("168000"), "discount_percent": 5,
              "discount_amount": D("100800"), "subtotal": D("2016000"), "tax_rate": 11},
             {"product_name": "Minyak 1L", "quantity": 30, "unit": "dus", "unit_price": D("172000"), "discount_percent": 0,
              "discount_amount": D("25000"), "subtotal": D("5160000"), "tax_rate": 11},
             {"product_name": "Gula", "quantity": 50, "unit": "sak", "unit_price": D("15500"), "discount_percent": 0,
              "discount_amount": D("0"), "subtotal": D("775000"), "tax_rate": 11}]
    return {"items": items, "subtotal": D("7951000"), "item_discount_total": D("125800"), "dpp": D("7825200"), "tax_rate": 11,
            "tax_amount": D("712000"), "total_amount": D("8537200"), "amount_paid": D("3000000"), "amount_due": D("5537200"),
            "cetak": {"jenis": "sebagian"}}


def test_faktur_penjualan_subtotal_sigma_neto_angka_akhir_tersimpan():
    rows = RC.faktur_penjualan(_si())
    labels = [r["label"] for r in rows]
    assert labels == ["Subtotal", "DPP", "PPN 11%", "Total", "Dibayar", "Sisa tagihan"] and "Diskon item" not in labels
    assert _nilai(rows, "Subtotal") == D("7825200") == sum(i["subtotal"] - i["discount_amount"] for i in _si()["items"])
    assert (_nilai(rows, "DPP"), _nilai(rows, "PPN 11%"), _nilai(rows, "Total"), _nilai(rows, "Sisa tagihan")) == \
        (D("7825200"), D("712000"), D("8537200"), D("5537200"))   # tersimpan, bukan dihitung


def test_tabel_si_kolom_total_neto_menjumlah_ke_subtotal():
    h = PS.get_pdf_service().render_sales_invoice({**_si(), "invoice_number": "INV-1", "status": "partial", "tenant": {"name": "U"},
                                                   "customer": {"name": "A"}, "has_cents": False}).html
    for teks in ("Rp 1.915.200", "Rp 5.135.000", "Rp 775.000", "Rp 25.000", "Rp 7.825.200", "Rp 8.537.200", "Rp 5.537.200"):
        assert teks in h, teks
    assert "Diskon item" not in h and "Rp 2.016.000" not in h and "Rp 7.951.000" not in h


@pytest.mark.parametrize("fn,dok,subtotal,akhir", [
    (RC.nota, {"subtotal": D("300000"), "item_discount_total": D("30000"), "discount_amount": D("27000"), "tax_rate": D("11"),
               "tax_amount": D("26730"), "total_amount": D("269730"), "items": [{"net": D("170000")}, {"net": D("100000")}]},
     D("270000"), {"Diskon": D("27000"), "PPN 11%": D("26730"), "Total": D("269730")}),
    (RC.faktur_pembelian, {"subtotal": "13400000.00", "item_discount_total": "618000.00", "invoice_discount_total": "150000.00",
                           "cash_discount_total": "0", "dpp": "12632000.00", "tax_rate": 11, "tax_amount": "1389520.00",
                           "grand_total": "14021520.00", "amount_paid": "5000000.00", "amount_due": "9021520.00", "cetak": {"jenis": "sebagian"},
                           "items": [{"total": "11172000.00"}, {"total": "500000.00"}, {"total": "1110000.00"}]},
     D("12782000"), {"Diskon faktur": D("150000"), "DPP": D("12632000"), "PPN 11%": D("1389520"), "Total": D("14021520"),
                     "Sisa tagihan": D("9021520")}),
    (RC.pesanan_pembelian, {"subtotal": D("12812000"), "discount_percent": 0, "discount_amount": D("150000"), "tax_amount": D("1392820"),
                            "label_pajak": "Pajak 11%", "total_amount": D("14054820"),
                            "items": [{"line_total": D("11172000")}, {"line_total": D("500000")}, {"line_total": D("1140000")}]},
     D("12812000"), {"Diskon": D("150000"), "Pajak 11%": D("1392820"), "Total": D("14054820")}),
])
def test_tiap_dokumen_subtotal_sigma_kolom_dan_akhir_tersimpan(fn, dok, subtotal, akhir):
    rows = fn(dok)
    assert rows[0] == {"label": "Subtotal", "nilai": subtotal, "kelas": ""} and "Diskon item" not in [r["label"] for r in rows]
    for label, nilai in akhir.items():
        assert _nilai(rows, label) == nilai, label
    assert all(r["kelas"] == "kurang" for r in rows if r["label"].startswith("Diskon"))   # potongan dicetak -Rp


def test_data_lama_tak_menjumlah_jatuh_ke_pola_lama_bukan_angka_karangan():
    """SIGMA neto baris != subtotal - diskon baris (pembulatan lama) -> Subtotal bruto + Diskon item (kertas tetap menjumlah)."""
    rows = RC.nota({"subtotal": D("300000"), "item_discount_total": D("30000"), "discount_amount": 0, "tax_amount": 0,
                    "total_amount": D("270000"), "items": [{"net": D("170001")}, {"net": D("100000")}]})
    assert [(r["label"], r["nilai"]) for r in rows[:2]] == [("Subtotal", D("300000")), ("Diskon item", D("30000"))]


def test_semua_templat_bertabel_harga_memakai_makro_ringkasan_satu_sumber():
    for f, fn in (("sales_invoice.html", "faktur_penjualan"), ("credit_note.html", "nota"), ("debit_note.html", "nota"),
                  ("bill_invoice.html", "faktur_pembelian"), ("purchase_order.html", "pesanan_pembelian"), ("quote.html", "penawaran"),
                  ("proforma.html", "proforma"), ("rekap_pesanan.html", "rekap")):
        s = re.sub(r"\{#.*?#\}", "", (PS.TEMPLATE_DIR / f).read_text(), flags=re.S)   # komentar Jinja boleh menyebut riwayat
        assert f"RC.{fn}(" in s and '{% from "_partials/ringkasan.html" import ringkasan %}' in s, f
        assert "Diskon item" not in s and 'class="summary-table"' not in s and '<div class="sum">' not in s, f   # blok lama dihapus


def test_faktur_sebagian_ppn_riwayat_ttd_sehalaman_dengan_ringkasan():
    """Putusan MASTER (2): Catatan + TTD sebaris ringkasan (pola FPB); riwayat sesudahnya; TTD tak yatim di halaman 2."""
    inv = {**_si(), "invoice_number": "INV-1", "status": "partial", "tenant": {"name": "Grapgrap", "logo_data": None},
           "customer": {"name": "A", "address": "Jl. B"}, "has_cents": False, "notes": "Catatan uji",
           "due_date": "2026-11-16", "invoice_date": "2026-11-02"}
    inv["cetak"] = {"jenis": "sebagian", "riwayat": [{"tanggal": "2026-11-03", "jenis": "Pembayaran", "metode": "Transfer BCA",
                                                      "nomor": "RCV-1", "jumlah": D("3000000")}], "tampil_rekening": False}
    r = PS.get_pdf_service().render_sales_invoice(inv)
    h = r.html
    assert h.index("Hormat kami,") < h.index('class="summary"') < h.index("Riwayat Pembayaran")
    fc = FontConfiguration()
    d = HTML(string=h).render(stylesheets=PS.get_pdf_service()._lembar_gaya(r, fc), font_config=fc)
    hal_ttd = next(i for i, p in enumerate(d.pages) if "Hormat kami" in str([b.text for b in _teks(p._page_box)]))
    hal_ring = next(i for i, p in enumerate(d.pages) if "Sisa tagihan" in str([b.text for b in _teks(p._page_box)]))
    assert hal_ttd == hal_ring, (hal_ttd, hal_ring)


def _teks(b):
    for c in getattr(b, "children", None) or []:
        if type(c).__name__ == "TextBox":
            yield c
        yield from _teks(c)


def test_proforma_komposisi_lalu_tagihan_satu_ringkasan():
    """Proforma (4 Nov 2026): Subtotal (= SIGMA neto baris SO) -> Diskon -> Ongkir -> PPN -> Nilai Pesanan -> baris tagihan (tebal);
    angka = kolom SO / proforma apa adanya (muat_pdf_proforma)."""
    p = {"purpose": "PELUNASAN", "order_items": [{"line_total": 1915200}, {"line_total": 775000}], "order_subtotal": 2690200,
         "order_discount": 25000, "order_shipping": 150000, "order_tax": 293172, "order_total_amount": 3108372, "amount": 1554186,
         "received_before": 1554186, "remaining_after_this": 0, "is_partially_paid": True, "paid_amount": 1000000, "outstanding_amount": 554186}
    rows = RC.proforma(p)
    assert [r["label"] for r in rows] == ["Subtotal", "Diskon", "Ongkos kirim", "PPN", "Nilai Pesanan", "Uang muka sudah diterima",
                                          "Pelunasan yang Ditagih", "Sisa setelah tagihan ini", "Sudah dibayar", "Sisa tagihan ini"]
    assert _nilai(rows, "Nilai Pesanan") == D("3108372") and _nilai(rows, "Pelunasan yang Ditagih") == D("1554186")
    assert "tebal" in next(r["kelas"] for r in rows if r["label"] == "Pelunasan yang Ditagih")
    assert [r["label"] for r in RC.proforma({"purpose": "DP", "amount": 100, "order_items": [], "order_total_amount": None})] == ["Uang Muka yang Ditagih"]


def test_proforma_ber_ttd_penutup_sama_dengan_si():
    """Proforma = dokumen tagih keluarga Penjualan -> penutup SAMA (_partials/penutup): Hormat kami + nama usaha, sebelum ringkasan."""
    r = PS.get_pdf_service().render_proforma({"proforma_number": "PRO-1", "purpose": "DP", "amount": D("100"), "order_items": [],
                                              "order_total_amount": None, "notes": "Catatan uji", "status": "posted"}, {"name": "Grapgrap"})
    h = r.html
    assert '<div class="penutup-si">' in h and "Hormat kami," in h and "Grapgrap" in h and "Catatan uji" in h
    assert h.index("Hormat kami,") < h.index('class="summary-table"')


def test_rekap_ringkasan_akun_tanpa_ttd():
    rows = RC.rekap({"total": D("3108372"), "dibayar": D("1554186"), "nota_kredit": D("100000"), "sisa": D("1454186")})
    assert [(r["label"], r["nilai"], r["kelas"]) for r in rows] == [("Total pesanan", D("3108372"), ""), ("Sudah dibayar", D("1554186"), "kurang"),
                                                                    ("Nota kredit", D("100000"), "kurang"), ("Sisa tagihan", D("1454186"), "sisa")]
    assert RC.rekap({"total": 1, "dibayar": 0, "sisa": 1})[1]["kelas"] == ""   # belum dibayar = "Rp 0", bukan "-Rp 0"
    assert "Hormat kami" not in (PS.TEMPLATE_DIR / "rekap_pesanan.html").read_text()   # putusan MASTER: Rekap TANPA TTD


def test_rekap_footer_sebaris_halaman_dan_subteks_menyebut_nota_kredit():
    """Rekap (tinjauan MASTER 4 Nov 2026): "Dicetak" = footer bersama invoice.css (margin bawah-kiri), bukan di badan; teks kecil di
    bawah angka besar menyebut nota kredit bila ada (angka sama dengan ringkasan)."""
    r = {"order_number": "SO-1", "status": "sebagian", "total": D("3108372"), "dibayar": D("1554186"), "nota_kredit": D("100000"),
         "sisa": D("1454186"), "tagihan": [], "pembayaran": []}
    h = PS.get_pdf_service().render_rekap_pesanan(r, {"name": "U"}).html
    assert '<span class="rk-seg">nota kredit Rp 100.000</span>' in h and '<div class="footer-line">Dicetak' in h   # segmen utuh nowrap
    assert "nota kredit Rp" not in PS.get_pdf_service().render_rekap_pesanan({**r, "nota_kredit": 0}, {"name": "U"}).html
    for f in ("sales_invoice.html", "credit_note.html", "proforma.html", "rekap_pesanan.html"):
        assert "running(dicetak)" not in (PS.TEMPLATE_DIR / f).read_text(), f   # tak ada salinan aturan footer per templat


def test_rekap_teks_kecil_satu_baris_bila_muat():
    """Tata letak HASIL WeasyPrint: tanpa stempel, teks kecil 3 segmen muat SATU baris (dulu terbungkus di kolom 64%, "Rp" terpisah)."""
    from weasyprint import HTML
    from weasyprint.text.fonts import FontConfiguration
    r = PS.get_pdf_service().render_rekap_pesanan({"order_number": "SO-1", "status": "sebagian", "total": D("3108372"), "dibayar": D("1554186"),
                                                   "nota_kredit": D("100000"), "sisa": D("1454186"), "tagihan": [], "pembayaran": []}, {"name": "U"})
    fc = FontConfiguration()
    d = HTML(string=r.html).render(stylesheets=PS.get_pdf_service()._lembar_gaya(r, fc), font_config=fc)
    garis = []
    def jalan(b):
        for c in getattr(b, "children", None) or []:
            if type(c).__name__ == "LineBox" and "nota kredit" in "".join(t.text for t in _teks(c)):
                garis.append("".join(t.text for t in _teks(c)))
            jalan(c)
    jalan(d.pages[0]._page_box)
    assert any("Total pesanan" in g and "nota kredit Rp 100.000" in g for g in garis), garis
