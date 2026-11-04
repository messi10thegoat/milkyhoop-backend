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
                  ("proforma.html", "proforma")):
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
