"""PDF KELUARGA DOKUMEN (3 Nov 2026, putusan MASTER; toko grosir): FPB ke kerangka PO + medan BE (kontak vendor bersama, No. PO,
No. faktur vendor, syarat bayar, riwayat pelunasan dari jurnal, keadaan_cetak SAMA dgn faktur penjualan), terbilang SATU fungsi,
NPWP tenant di kop, PO (alamat gudang, terbilang, tanpa subjudul Inggris), SI (jatuh tempo tidak ganda, terbilang, TTD).
Render lewat jalur NYATA (PDFService + Jinja) dengan WeasyPrint diganti penangkap string; pemuat lewat koneksi palsu per SQL."""
import asyncio
import re
from datetime import date
from decimal import Decimal

import pytest

from app.services import cetak_bill as CB
from app.services import faktur_cetak as FC
from app.services import pdf_service as PS
from app.services import po_cetak as PC
from app.services import rekap_pesanan as RP
from app.utils.terbilang import terbilang_rupiah

HARI = date(2026, 11, 3)


@pytest.fixture
def tangkap(monkeypatch):
    html = {}

    class _HTML:
        def __init__(self, string=None, **kw):
            html["isi"] = string

        def write_pdf(self, **kw):
            return b"%PDF"
    monkeypatch.setattr(PS, "HTML", _HTML)
    return html


class _Conn:
    """Jawab per pola SQL; pola tak dikenal = galat (stub tak menebak)."""
    def __init__(self, peta):
        self.peta, self.sql = peta, []

    def _cari(self, q):
        self.sql.append(q)
        for pola, v in self.peta:
            if pola in q:
                return v
        raise AssertionError("SQL tak dikenal: " + " ".join(q.split())[:120])

    async def fetchrow(self, q, *a):
        return self._cari(q)

    async def fetch(self, q, *a):
        return self._cari(q)

    async def fetchval(self, q, *a):
        return self._cari(q)


KOP = {"display_name": "Toko Grosir Sinar Jaya", "address": "Jl. Pasar Baru 12", "phone": "0812 1", "logo_url": None, "tax_id": " 02.345.678.9-012.000 "}
VEN = {"name": "PT Sumber Minyak", "address": "Jl. Industri C-7", "city": "Bitung", "province": None, "postal_code": None,
       "phone": None, "mobile_phone": "0811 2", "email": None, "tax_id": "01.234.567.8-901.000"}


def _bill(**x):
    b = {"id": "11111111-1111-1111-1111-111111111111", "invoice_number": "BILL-2611-0005", "ref_no": "INV/SMN/0451", "status": "partial",
         "vendor": {"id": "v1", "name": "PT Sumber Minyak", "initials": "PS"}, "vendor_id": "v1", "vendor_name": "PT Sumber Minyak",
         "issue_date": "2026-11-03", "due_date": "2026-12-03", "voided_at": None, "tax_rate": 11,
         "items": [{"product_name": "Minyak Kita 1L", "qty": 100.0, "unit": "Dus", "price": "174000.00", "discount_percent": 2.0, "total": "17052000.00"},
                   {"product_name": "Bimoli 2L", "qty": 50.0, "unit": "Dus", "price": "310000.00", "discount_percent": 0.0, "total": "15500000.00",
                    "batch_no": "B77", "exp_date": "2027-05-01"}],
         "subtotal": "32552000.00", "dpp": "32552000.00", "tax_amount": "3580720.00", "grand_total": "36132720.00", "amount": "36132720.00",
         "amount_paid": "20000000.00", "amount_due": "16132720.00", "notes": None,
         "invoice_discount_total": "0", "cash_discount_total": "0"}
    b.update(x)
    return b


RIWAYAT = [{"sumber": "pembayaran", "tanggal": HARI, "nomor": "BP-2611-0007", "metode": "bank_transfer", "bank": "BCA", "jumlah": Decimal("20000000")},
           {"sumber": "nota_debit", "tanggal": HARI, "nomor": "VC-1", "metode": None, "bank": None, "jumlah": Decimal("0")}]


def _conn_bill(riwayat=RIWAYAT, po="PO-2611-0002", ven=VEN):
    return _Conn([('FROM "Tenant"', KOP), ("FROM vendors WHERE id", ven), ("JOIN purchase_orders po ON po.id = b.purchase_order_id", po),
                  ("WITH pd_bayar AS", riwayat)])


@pytest.fixture
def hari(monkeypatch):
    import app.utils.tanggal_tenant as TT

    async def _t(conn, tid):
        return HARI

    async def _z(conn, tid):
        return None
    monkeypatch.setattr(TT, "tanggal_dokumen", _t)
    monkeypatch.setattr(TT, "zona_tenant", _z)


def _lengkap(b, conn=None):
    return asyncio.run(CB.lengkapi_cetak_bill(conn or _conn_bill(), "t1", b))


# ---------------------------------------------------------------- fungsi murni
@pytest.mark.parametrize("nilai,kata", [
    (0, "Nol Rupiah"), ("174000000.00", "Seratus Tujuh Puluh Empat Juta Rupiah"),
    ("100.50", "Seratus Rupiah Lima Puluh Sen"), ("100.005", "Seratus Rupiah Satu Sen"), (-1500, "Minus Seribu Lima Ratus Rupiah"),
])
def test_terbilang_rupiah(nilai, kata):
    assert terbilang_rupiah(nilai) == kata


@pytest.mark.parametrize("a,b,teks", [("2026-11-03", "2026-11-03", "Tunai"), ("2026-11-03", "2026-12-03", "30 hari"),
                                      (date(2026, 11, 3), date(2026, 11, 17), "14 hari"), (None, "2026-11-03", None), ("2026-11-05", "2026-11-03", None)])
def test_label_syarat(a, b, teks):
    assert CB.label_syarat(a, b) == teks


def test_sql_riwayat_tagihan_cermin_cabang_ap_rincian():
    """Tiga sumber pelunasan compute_ap_rincian (V440) dengan predikat jurnal SAMA -> jumlah riwayat == amount_paid."""
    q = " ".join(CB.SQL_RIWAYAT_TAGIHAN.split())
    for f in ("FROM bill_payments_v2", "FROM vendor_credit_applications", "FROM vendor_deposit_applications",
              "je.source_type = 'DEPOSIT_APPLICATION'", "coa.account_type = 'PAYABLE' AND jl.debit > 0"):
        assert f in q, f
    assert q.count("je.status = 'POSTED' AND je.reversed_by_id IS NULL") == 3
    assert q.count("$1") >= 6   # tenant eksplisit di tiap cabang


# ---------------------------------------------------------------- pemuat
def test_kop_npwp_dari_tax_id():
    kop = asyncio.run(RP.muat_kop(_Conn([('FROM "Tenant"', KOP)]), "t1"))
    assert kop["npwp"] == "02.345.678.9-012.000"
    kosong = asyncio.run(RP.muat_kop(_Conn([('FROM "Tenant"', {**KOP, "tax_id": "  "})]), "t1"))
    assert kosong["npwp"] is None


def test_kontak_vendor_satu_fungsi_po_dan_fpb():
    k = asyncio.run(PC.kontak_vendor(_Conn([("FROM vendors WHERE id", VEN)]), "t1", "v1"))
    assert k == {"nama": "PT Sumber Minyak", "alamat": "Jl. Industri C-7\nBitung", "telepon": "0811 2", "npwp": "01.234.567.8-901.000"}
    assert asyncio.run(PC.kontak_vendor(_Conn([]), "t1", None)) == {"nama": None, "alamat": None, "telepon": None, "npwp": None}


def test_lengkapi_cetak_bill_medan(hari):
    conn = _conn_bill()
    b = _lengkap(_bill(), conn)
    assert b["vendor_kontak"]["npwp"] == "01.234.567.8-901.000" and b["po_number"] == "PO-2611-0002"
    assert b["syarat_bayar"] == "30 hari" and b["tenant"]["npwp"] == "02.345.678.9-012.000"
    c = b["cetak"]
    assert c["jenis"] == "sebagian" and c["selisih_riwayat"] == 0
    assert [(h["jenis"], h["metode"], h["nomor"]) for h in c["riwayat"]] == [("Pembayaran", "Transfer BCA", "BP-2611-0007")]   # jumlah 0 dibuang
    assert b["has_cents"] is False
    assert all("tenant_id = $2" in q or "$1" in q for q in conn.sql)


def test_lengkapi_lunas_tanggal_metode_dari_riwayat(hari):
    b = _lengkap(_bill(status="paid", amount_paid="36132720.00", amount_due="0.00"),
                 _conn_bill([{**RIWAYAT[0], "jumlah": Decimal("36132720"), "metode": "cash", "bank": None}]))
    c = b["cetak"]
    assert (c["jenis"], c["stempel"], c["tanggal_lunas"], c["metode_lunas"]) == ("lunas", "LUNAS", HARI, "Tunai")


# ---------------------------------------------------------------- templat FPB
def _fpb(tangkap, hari_fx, **x):
    PS.get_pdf_service().generate_bill_pdf(_lengkap(_bill(**x)))
    return tangkap["isi"]


def test_fpb_pola_accurate_isi(tangkap, hari):
    """FPB = catatan internal (pola Accurate 4 Nov 2026): kop usaha di atas, TANPA "Kepada", No. Form + No. Faktur vendor, TTD Bagian Pembelian."""
    h = _fpb(tangkap, hari)
    for teks in ("<h1>Faktur Pembelian</h1>", "<b>No. Form</b>", "<b>No. Faktur</b><span>INV/SMN/0451</span>", "<b>No. PO</b><span>PO-2611-0002</span>",
                 "<b>Syarat bayar</b><span>30 hari</span>", "NPWP 01.234.567.8-901.000", "0811 2", "NPWP 02.345.678.9-012.000",
                 "100 Dus", "50 Dus", '<th class="r">Diskon</th>', ">2%</td>", '<td class="r">-</td>', "Batch B77 · Exp 2027-05-01",
                 "Riwayat pembayaran", "BP-2611-0007", "Pembayaran &middot; Transfer BCA", "Bagian Pembelian,", '<div class="garis">Tgl.</div>',
                 terbilang_rupiah("36132720.00"), "<span>Sebagian dibayar</span>"):
        assert teks in h, teks
    for tak in ('class="hero', "<b>Kepada</b>", "Diterima oleh", "No. faktur vendor", "Referensi", "Satuan</th>", "Batch</th>", "Exp</th>", "Bonus</th>", "0%</td>"):
        assert tak not in h, tak
    assert "penawaran.css" in PS.CSS_PENAWARAN


def test_fpb_diskon_selalu_kode_hanya_bila_sku_noref_strip(tangkap, hari):
    """Pola Accurate: kolom Diskon SELALU ("-"); Kode HANYA bila ada SKU; No. Faktur vendor kosong = "—"; TTD bisa disembunyikan."""
    items = [{"product_name": "Minyak", "qty": 1.0, "unit": "Dus", "price": "100.00", "discount_percent": 0.0, "total": "100.00"}]
    kw = dict(items=items, subtotal="100.00", dpp="0", tax_amount="0", tax_rate=0, grand_total="100.00", amount="100.00",
              amount_paid="0.00", amount_due="100.00", ref_no=None)
    h = _fpb(tangkap, hari, **kw)
    assert '<th class="r">Diskon</th>' in h and '<td class="r">-</td>' in h and "<th>Kode</th>" not in h
    assert "<b>No. Faktur</b><span>—</span>" in h and "Belum dibayar" in h and "Dibayar</span>" not in h
    h = _fpb(tangkap, hari, **{**kw, "items": [{**items[0], "product_code": "MNY-1"}]})
    assert "<th>Kode</th>" in h and "<td>MNY-1</td>" in h
    assert h.index("@Harga</th>") < h.index("Diskon</th>") < h.index("Total</th>")   # pola Accurate; PO/Penawaran tetap Diskon dulu
    assert "Bagian Pembelian" not in PS.get_pdf_service().render_bill(_lengkap(_bill(**kw)), tampil_ttd=False).html


def test_fpb_batal_stempel_dan_total_dicoret(tangkap, hari):
    h = _fpb(tangkap, hari, status="void", voided_at="2026-11-03T05:00:00", voided_reason="salah input")
    assert 'stempel-teks">BATAL' in h and 'class="coret">Rp 36.132.720' in h and "tidak berlaku sebagai tagihan" in h
    assert "Dibayar</span>" not in h


def test_rute_lama_bills_pdf_memakai_pelengkap_sama():
    src = (PS.TEMPLATE_DIR.parent.parent / "routers" / "bills.py").read_text()
    blok = src[src.index('@router.get("/{bill_id}/pdf")'):]
    blok = blok[:blok.index("\n@router.")]
    assert "lengkapi_cetak_bill" in blok and 'FROM "Tenant"' not in blok


# ---------------------------------------------------------------- PO + SI
def _po_row(**x):
    r = {"id": "po1", "po_number": "PO-2611-0002", "status": "sent", "po_date": HARI, "expected_date": date(2026, 11, 6), "ref_no": None,
         "ship_to_address": None, "warehouse_name": "Gudang Utama", "warehouse_address": "Jl. Pelabuhan 3", "warehouse_city": "Manado",
         "notes": None, "cancelled_at": None, "cancelled_reason": None, "vendor_id": "v1", "vendor_name": "PT Sumber Minyak",
         "subtotal": Decimal("100"), "discount_percent": 0, "discount_amount": 0, "tax_amount": 0, "tax_rate": 0, "total_amount": Decimal("100")}
    r.update(x)
    return r


def test_po_keluarga(tangkap):
    items = [{"description": "Minyak", "item_code": None, "quantity": Decimal("1"), "unit": "Dus", "unit_price": Decimal("100"),
              "discount_percent": Decimal("0"), "discount_amount": Decimal("0"), "subtotal": Decimal("100"), "total": Decimal("100")}]
    conn = _Conn([("FROM purchase_orders po LEFT JOIN warehouses", _po_row()), ("FROM vendors WHERE id", VEN),
                  ("FROM purchase_order_items", items), ('FROM "Tenant"', KOP)])
    m = asyncio.run(PC.muat_pdf_po(conn, {"tenant_id": "t1"}, "po1"))
    assert m["po_data"]["warehouse_alamat"] == "Jl. Pelabuhan 3\nManado" and m["po_data"]["vendor_npwp"] == "01.234.567.8-901.000"
    ps = PS.get_pdf_service()
    ps.tulis_pdf(ps.render_purchase_order(m["po_data"], m["tenant_info"]))
    h = tangkap["isi"]
    # PO = TURUNAN FPB pola Accurate (4 Nov 2026): kop usaha, Kepada vendor + Kirim ke gudang, meta No. PO, tabel konfigurasi FPB, TTD Pemesan
    for teks in ('<div class="kop">', "<b>Kepada</b>", '<b class="kirim">Kirim ke</b>', "<b>No. PO</b>", "<b>Jadwal terima</b>", "Jl. Pelabuhan 3",
                 "NPWP 02.345.678.9-012.000", "Seratus Rupiah", "Pemesan,", '<div class="garis">Bagian Pembelian</div>',
                 "<th>Nama Barang</th>", '<th class="r">Kts.</th>', '<th class="r">Diskon</th>'):
        assert teks in h, teks
    assert h.index("@Harga</th>") < h.index("Diskon</th>") and "<th>Kode</th>" not in h
    for tak in ("Purchase Order", "Tanggal kirim", "Syarat bayar", "<b>Nomor</b>"):
        assert tak not in h, tak


def _si(**x):
    inv = {"invoice_number": "INV-1", "status": "posted", "invoice_date": "2026-10-01", "due_date": "2026-10-11", "customer": {"name": "Toko A"},
           "tenant": {"name": "Toko Grosir"}, "items": [{"product_name": "Minyak", "quantity": 1, "unit_price": 100, "subtotal": 100}],
           "subtotal": 100, "total_amount": 100, "amount_paid": 0, "amount_due": 100, "has_cents": False}
    inv.update(x)
    inv["cetak"] = FC.keadaan_cetak(inv, HARI, [])
    return inv


def test_si_keluarga_tanpa_subjudul_terbilang_ttd(tangkap):
    PS.get_pdf_service().generate_sales_invoice_pdf(_si())
    h = tangkap["isi"]
    assert "doc-subtitle" not in h and ">Invoice<" not in h
    assert 'class="terbilang">Seratus Rupiah' in h and "Hormat kami," in h
    i = h.index('class="hero-teks"')
    hero = h[i:i + 900]
    assert "terlambat 23 hari" in hero and "11 Okt 2026" not in hero   # jatuh tempo hanya di meta, tidak ganda


def test_si_belum_jatuh_tempo_baris_hero_tanpa_tanggal(tangkap):
    PS.get_pdf_service().generate_sales_invoice_pdf(_si(due_date="2026-11-13"))
    h = tangkap["isi"]
    i = h.index('class="hero-teks"')
    hero = h[i:i + 900]
    assert "10 hari lagi" in hero and "Jatuh tempo 13 Nov" not in hero
    assert h.count("13 Nov 2026") == 1   # meta saja


def test_fpb_satu_halaman_ttd_di_kolom_keterangan(hari):
    """Tata letak HASIL WeasyPrint: FPB ber-riwayat + pajak + diskon muat 1 halaman; TTD di kolom kiri ringkasan (dulu TTD sendirian di hal. 2)."""
    from weasyprint import HTML
    from weasyprint.text.fonts import FontConfiguration
    b = _bill(notes="Kain diterima 2 tahap.")
    b["items"] = b["items"] + [{**b["items"][0], "product_name": "Baris ke-%d" % i} for i in range(3, 5)]   # 4 baris (kasus nyata 3)
    r = PS.get_pdf_service().render_bill(_lengkap(b))
    fc = FontConfiguration()
    d = HTML(string=r.html).render(stylesheets=PS.get_pdf_service()._lembar_gaya(r, fc), font_config=fc)
    assert len(d.pages) == 1, len(d.pages)
    assert r.html.index("Bagian Pembelian,") < r.html.index('<div class="sum">') < r.html.index("Riwayat pembayaran")


def test_fpb_catatan_otomatis_konversi_po_tak_dicetak(tangkap, hari):
    """Catatan otomatis routers/purchase_orders "Dari pesanan pembelian <No. PO>" = dobel meta No. PO -> tak dicetak (data tetap); catatan lain tetap."""
    assert "Dari pesanan pembelian PO-2611-0002" not in _fpb(tangkap, hari, notes="Dari pesanan pembelian PO-2611-0002")
    assert "Dari pesanan pembelian PO-2611-0001" in _fpb(tangkap, hari, notes="Dari pesanan pembelian PO-2611-0001")


def test_surat_jalan_dokumen_gudang_kerangka_fpb_tanpa_harga():
    """PDF gudang (4 Nov 2026, putusan MASTER): SJ = dokumen_gudang.html SATU templat (GRN menyusul) -- kop_usaha, Kepada + Kirim ke |
    meta, tabel_barang TANPA kolom uang (Keterangan hanya bila ada catatan baris), Catatan, 3 TTD garis; "Delivery Note" dibuang."""
    ps = PS.get_pdf_service()
    d = {"delivery_number": "SJ-2611-0007", "delivery_date": "2026-11-04", "invoice_number": "INV-1", "customer_name": "Toko Sinar",
         "customer_address": "Jl. A 1", "customer_phone": "0812", "notes": "Bongkar belakang", "tenant": {"name": "Grosir", "npwp": "01.2"},
         "serah": {"metode": {"nama": "Kurir", "jenis": "kirim"}, "kirim_ke": "Gudang Toko\nBitung", "kirim_ke_sumber": "manual",
                   "resi": [{"nomor": "KT-1"}], "warehouse_name": "Utama"},
         "items": [{"product_name": "Minyak 2L", "product_sku": "MNY-2", "quantity": "120", "unit": "dus", "notes": "Dus penyok"}]}
    h = ps.render_delivery_note(d).html
    for teks in ("<h1>Surat Jalan</h1>", '<div class="kop">', "NPWP 01.2", "<b>Kepada</b>", '<b class="kirim">Kirim ke</b>', "Gudang Toko",
                 "<b>No. Surat Jalan</b><span>SJ-2611-0007</span>", "<b>No. Resi</b><span>KT-1</span>", "<th>Kode</th>", "120 dus",
                 ">Keterangan</th>", "Dus penyok", "Bongkar belakang", "Diterima oleh,", "Pengantar,", "Gudang,"):
        assert teks in h, teks
    for tak in ("Delivery Note", "Harga", "Rp ", "( ____", "Dokumen ini sah"):
        assert tak not in h, tak
    assert h.count('<div class="sign">') == 3 and ps.render_delivery_note(d).css == PS.CSS_PENAWARAN
    d["items"][0]["notes"] = None
    assert ">Keterangan</th>" not in ps.render_delivery_note(d).html
    assert not (PS.TEMPLATE_DIR / "delivery_note.html").exists()   # digantikan dokumen_gudang.html (dihapus, bukan disimpan)


def test_nota_kredit_bingkai_si_tabel_bersama_tanpa_hero_ttd():
    """PDF seragam 4 Nov 2026 (putusan MASTER): NK = bingkai SI (invoice.css) + tabel_barang bersama + terbilang + TTD "Hormat kami";
    angka besar & kalimat "Kredit untuk pelanggan" DIBUANG (simetri ND lewat isi); penutup SI satu sumber di invoice.css."""
    ps = PS.get_pdf_service()
    cn = {"credit_note_number": "CN-1", "status": "posted", "customer_name": "Toko", "original_invoice_number": "INV-1", "reason_label": "Retur",
          "reason_detail": "Bocor", "subtotal": 300000, "discount_amount": 0, "tax_amount": 0, "total_amount": 270000, "item_discount_total": 30000,
          "has_cents": False, "items": [{"description": "Minyak", "quantity": 2, "unit": "dus", "unit_price": 150000, "discount_amount": 30000, "net": 270000}]}
    r = ps.render_credit_note(cn, {"name": "Grosir"})
    h = r.html
    for teks in ('<table class="tabel-barang items-table">', "<th>Nama Barang</th>", '<th class="r">Kts.</th>', '<th class="r">Diskon</th>',
                 "Rp 30.000", "Rp 270.000", "2 dus",
                 '<div class="terbilang">', "Hormat kami,", "Grosir", "Keterangan Alasan", "Faktur Asal"):
        assert teks in h, teks
    for tak in ('class="hero', "Kredit untuk pelanggan", 'class="col-desc"'):
        assert tak not in h, tak
    assert h.index("@Harga</th>") < h.index("Diskon</th>") < h.index(">Total</th>")   # urutan = PO/FPB/ND
    css = (PS.TEMPLATE_DIR / "invoice.css").read_text()
    # penutup keluarga Penjualan SATU sumber (_partials/penutup: SI, NK, Proforma) -- Catatan + TTD kiri sebaris ringkasan
    assert ".penutup-si {" in css and ".ttd .garis" in css and ".penutup {" not in css
    for f in ("sales_invoice.html", "credit_note.html", "proforma.html"):
        s = (PS.TEMPLATE_DIR / f).read_text()
        assert '{% from "_partials/penutup.html" import penutup %}' in s and "Hormat kami" not in s, f


def test_surat_jalan_meta_referensi_hanya_bila_ada():
    """MASTER 4 Nov 2026: "No. Faktur" kosong tak dicetak (dulu "-"); SJ dari pesanan mencetak "No. Pesanan SO-…"."""
    ps = PS.get_pdf_service()
    d = {"delivery_number": "SJ-1", "items": [], "tenant": {"name": "U"}, "invoice_number": None, "sales_order_number": "SO-2611-0009"}
    h = ps.render_delivery_note(d).html
    assert "<b>No. Pesanan</b><span>SO-2611-0009</span>" in h and "No. Faktur" not in h
    h = ps.render_delivery_note({**d, "invoice_number": "INV-1", "sales_order_number": None}).html
    assert "<b>No. Faktur</b><span>INV-1</span>" in h and "No. Pesanan" not in h
