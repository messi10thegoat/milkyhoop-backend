"""P2P cetak (25 Okt 2026, MASTER: simetri Pembelian): /api/documents/{bill|payment_out|debit_note}/{id}/(html|pdf).

Penjaga: (1) jenis cetak terdaftar (satu per baris, urut abjad -- pola bersama BACKEND/BACKEND2), BUKAN jenis bagikan
(document_shares.kind = CHECK V379, butuh migrasi + izin pemilik); (2) izin R per jenis = modul rute aslinya;
(3) render: angka TERSIMPAN dicetak apa adanya dan menjumlah ke Total; stempel DRAF / DIBATALKAN; kwitansi keluar
memakai templat kwitansi yang SAMA dengan label keluar (penerimaan tetap DITERIMA); (4) pemuat memfilter tenant;
(5) faktur pembelian SATU render untuk rute lama /api/bills/{id}/pdf dan /api/documents/bill."""
import ast
import asyncio
import inspect
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from fastapi import HTTPException

from app.middleware import permission_middleware as PM
from app.routers import dokumen as DK
from app.services import bills_service as BS
from app.services import cetak_bill as CB
from app.services import cetak_debit_note as CD
from app.services import cetak_payment_out as CP
from app.services.pdf_service import get_pdf_service

TENANT = {"name": "Kaos Biru Konveksi", "address": "Jl. Contoh 1", "phone": "0812", "logo_data": None}
BARU = ("bill", "debit_note", "payment_out")


# ── pendaftaran + izin ──────────────────────────────────────────────────────────────────────────────────────────

def test_jenis_cetak_terdaftar_urut_abjad_dan_bukan_jenis_bagikan():
    tambahan = DK.JENIS_CETAK[len(DK.JENIS):]
    assert DK.JENIS_CETAK[:len(DK.JENIS)] == DK.JENIS
    assert list(tambahan) == sorted(tambahan) and len(set(tambahan)) == len(tambahan)
    for j in BARU:
        assert j in tambahan and j not in DK.JENIS  # bagikan = migrasi document_shares.kind, BELUM


@pytest.mark.parametrize("jenis,izin", [("bill", "purchase_invoice"), ("payment_out", "send_payment"),
                                        ("debit_note", "debit_note")])
def test_izin_baca_per_jenis(jenis, izin):
    f = PM.PermissionMiddleware(lambda *a: None, False)._find_permission
    for akhir in ("html", "pdf"):
        assert f(f"/api/documents/{jenis}/X/{akhir}", "GET") == (izin, "R")
    if jenis == "bill":  # L7 (31 Okt): Faktur Pembelian bisa dibagikan, izin E modulnya sendiri
        assert f(f"/api/documents/{jenis}/X/share", "POST") == (izin, "E") and jenis in DK.JENIS_BAGIKAN
    else:
        assert f(f"/api/documents/{jenis}/X/share", "POST") != (izin, "E") and jenis not in DK.JENIS_BAGIKAN  # bagikan tak dibuka


def test_render_mengenal_ketiga_jenis_lewat_pemuat_sendiri():
    src = inspect.getsource(DK._render)
    for j, mod, fn in (("bill", "cetak_bill", "render_bill"), ("payment_out", "cetak_payment_out", "render_payment_out"),
                       ("debit_note", "cetak_debit_note", "render_debit_note")):
        i = src.index(f'if jenis == "{j}":')
        blok = src[i:i + 400]
        assert f"services.{mod} import" in blok and f"ps.{fn}(" in blok, j


# ── faktur pembelian: satu sumber ───────────────────────────────────────────────────────────────────────────────

def test_rute_lama_dan_dokumen_satu_render():
    from app.services.pdf_service import PDFService
    assert "self.render_bill(" in inspect.getsource(PDFService.generate_bill_pdf)
    assert "get_bill_v2_conn(conn, tenant_id, bill_id)" in inspect.getsource(BS.BillsService.get_bill_v2)
    pemanggil = {n.func.attr for n in ast.walk(ast.parse(inspect.getsource(CB)))
                 if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
    assert "get_bill_v2_conn" in pemanggil and "acquire" not in pemanggil  # Law 32: tanpa acquire bersarang


def _bill(**ubah):
    d = {"invoice_number": "PI-2610-0001", "status": "unpaid", "vendor": {"name": "CV Benang Jaya"}, "vendor_name": "CV Benang Jaya",
         "issue_date": "2026-10-20", "due_date": "2026-11-19", "subtotal": "1000000", "amount": "1110000",
         "grand_total": "1110000", "amount_paid": "0", "amount_due": "1110000", "tax_rate": 11, "tax_amount": "110000",
         "dpp": "1000000", "item_discount_total": "0", "invoice_discount_total": "0", "cash_discount_total": "0",
         "notes": None, "ref_no": None, "voided_at": None, "voided_reason": None, "tenant": TENANT,
         "items": [{"product_name": "Benang", "qty": 10.0, "unit": None, "price": "100000", "total": "1000000",
                    "discount_percent": 0, "batch_no": None, "exp_date": None, "bonus_qty": 0}]}
    d.update(ubah)
    return d


def test_render_bill_font_dokumen_dan_stempel():
    ps = get_pdf_service()
    r = ps.render_bill(_bill())
    assert r.font is True and "fonts.css" in r.css
    for teks in ("Faktur Pembelian", "PI-2610-0001", "CV Benang Jaya", "Kaos Biru Konveksi"):
        assert teks in r.html, teks
    assert "DRAF" not in r.html and "DIBATALKAN" not in r.html
    assert "DRAF" in ps.render_bill(_bill(status="draft")).html
    h_void = ps.render_bill(_bill(status="void", voided_at=datetime(2026, 10, 21, 3, tzinfo=timezone.utc),
                                  voided_reason="salah input")).html
    assert "DIBATALKAN" in h_void and "salah input" in h_void


# ── bukti pembayaran keluar ─────────────────────────────────────────────────────────────────────────────────────

class _Conn:
    def __init__(self, baris: dict, daftar: list):
        self.baris, self.daftar, self.sql = baris, daftar, []

    async def fetchrow(self, sql, *a):
        self.sql.append((sql, a))
        if 'FROM "Tenant"' in sql:
            return {"display_name": "Usaha", "address": None, "phone": None, "logo_url": None}
        for tabel, row in self.baris.items():
            if f"FROM {tabel} " in sql:
                return row if (len(a) < 2 or a[1] == "t1") else None
        return None

    async def fetch(self, sql, *a):
        self.sql.append((sql, a))
        return self.daftar


def _pay(**ubah):
    d = {"id": uuid.uuid4(), "payment_number": "PAY-2610-0004", "reference_number": None, "vendor_name": "CV Benang Jaya",
         "payment_date": date(2026, 10, 22), "payment_method": "bank_transfer", "bank_account_id": None,
         "bank_account_name": "BCA Operasional", "total_amount": Decimal("1110000.00"), "status": "posted",
         "voided_at": None, "void_reason": None, "notes": None}
    d.update(ubah)
    return d


def _muat_bayar(pay, alokasi, tid="t1"):
    return asyncio.run(CP.muat_pdf_payment_out(_Conn({"bill_payments_v2": pay}, alokasi), {"tenant_id": tid}, pay["id"]))


def test_pemuat_bayar_label_keluar_faktur_dan_sisa_satu_faktur():
    d = _muat_bayar(_pay(), [{"invoice_number": "PI-2610-0001", "remaining_after": 0}])["receipt_data"]
    assert d["title"] == "Bukti Kas Keluar" and d["subtitle"] == "Pemberitahuan pembayaran ke pemasok"
    assert d["hide_stamp"] is True and "stamp_label" not in d and d["party_label"] == "Dibayarkan Kepada"
    assert d["purpose_ref"] == "PI-2610-0001" and d["remaining"] == Decimal("0") and isinstance(d["amount"], Decimal)
    d2 = _muat_bayar(_pay(), [{"invoice_number": "PI-1", "remaining_after": 5}, {"invoice_number": "PI-2", "remaining_after": 0}])
    assert d2["receipt_data"]["purpose_ref"] == "PI-1, PI-2" and d2["receipt_data"]["remaining"] is None


def test_render_bayar_keluar_dan_kwitansi_penerimaan_tetap():
    ps = get_pdf_service()
    d = _muat_bayar(_pay(), [{"invoice_number": "PI-2610-0001", "remaining_after": 0}])
    r = ps.render_payment_out(d["receipt_data"], TENANT)
    for teks in ("Bukti Kas Keluar", "Pemberitahuan pembayaran ke pemasok", "Dibayarkan Kepada", "CV Benang Jaya", "Jumlah Dibayar",
                 "Rp 1.110.000", "PI-2610-0001", "No. Pembayaran", "PAY-2610-0004", "Dibayar oleh,"):
        assert teks in r.html, teks
    # BKK (30 Okt 2026): TANPA stempel -- bukan DIBAYAR, dan stempel kosong TAK jatuh ke DITERIMA
    for teks in ("DITERIMA", "DIBAYAR", "kw-stempel-diterima", "Telah Terima Dari", "Bukti Pembayaran"):
        assert teks not in r.html, teks
    h_draf = ps.render_payment_out(_muat_bayar(_pay(status="draft"), [])["receipt_data"], TENANT).html
    assert "kw-stempel-diterima" not in h_draf and "DIBAYAR" not in h_draf and "DITERIMA" not in h_draf
    h_void = ps.render_payment_out(_muat_bayar(_pay(status="voided", voided_at=datetime(2026, 10, 23, tzinfo=timezone.utc),
                                                    void_reason="dobel"), [])["receipt_data"], TENANT).html
    assert "DIBATALKAN" in h_void and "dobel" in h_void
    # kwitansi penerimaan (tanpa stamp_label) TAK bergeser
    terima = ps.render_receipt({"status": "posted", "receipt_number": "RCV-1", "receipt_date": "2026-10-01",
                                "payer_name": "X", "amount": 1000, "method": "Tunai"}, TENANT).html
    assert "DITERIMA" in terima and "DIBAYAR" not in terima and "kw-stempel-diterima" in terima
    # kontrol: hide_stamp hanya dibaca bila dikirim -- kwitansi penerimaan berstempel kosong tetap DITERIMA
    terima2 = ps.render_receipt({"status": "posted", "receipt_number": "RCV-2", "receipt_date": "2026-10-01", "payer_name": "Y",
                                 "amount": 1000, "method": "Tunai", "stamp_label": ""}, TENANT).html
    assert "DITERIMA" in terima2 and "kw-stempel-diterima" in terima2


# ── nota debit ──────────────────────────────────────────────────────────────────────────────────────────────────

def _vc(**ubah):
    d = {"id": uuid.uuid4(), "credit_number": "VC-2610-0002", "credit_date": date(2026, 10, 24), "status": "posted",
         "voided_at": None, "voided_reason": None, "vendor_name": "CV Benang Jaya", "original_bill_id": None,
         "original_bill_number": "PI-2610-0001", "reason": "return", "reason_detail": "Benang putus", "ref_no": None,
         "notes": None, "subtotal": Decimal("300000.00"), "discount_amount": Decimal("27000.00"), "tax_rate": Decimal("11"),
         "tax_amount": Decimal("26730.00"), "total_amount": Decimal("269730.00")}
    d.update(ubah)
    return d


ITEM_DISKON = [{"description": "Benang Hitam", "quantity": Decimal("2"), "unit": "rol", "unit_price": Decimal("100000"),
                "discount_amount": Decimal("30000"), "subtotal": Decimal("200000")},
               {"description": "Benang Putih", "quantity": Decimal("1"), "unit": "rol", "unit_price": Decimal("100000"),
                "discount_amount": Decimal("0"), "subtotal": Decimal("100000")}]


def _muat_nd(vc, items, tid="t1"):
    return asyncio.run(CD.muat_pdf_debit_note(_Conn({"vendor_credits": vc, "bills": None}, items), {"tenant_id": tid}, vc["id"]))


def _kertas_menjumlah(d):
    neto = sum((i["net"] for i in d["items"]), Decimal("0"))
    return (neto == d["subtotal"] - d["item_discount_total"]
            and d["subtotal"] - d["item_discount_total"] - d["discount_amount"] + d["tax_amount"] == d["total_amount"])


def test_nd_total_tersimpan_mengurangi_diskon_baris_maka_dicetak():
    # 300.000 - 30.000 - 27.000 + 26.730 = 269.730 (total tersimpan memang mengurangi diskon baris)
    d = _muat_nd(_vc(), ITEM_DISKON)["debit_note_data"]
    assert d["item_discount_total"] == Decimal("30000") and d["items"][0]["net"] == Decimal("170000")
    assert _kertas_menjumlah(d)


def test_nd_total_tersimpan_mengabaikan_diskon_baris_maka_bruto_supaya_tetap_menjumlah():
    # rumus header _susun_nd: subtotal(bruto) - diskon dokumen + PPN -> diskon baris TIDAK mengurangi total tersimpan
    vc = _vc(tax_amount=Decimal("30030.00"), total_amount=Decimal("303030.00"))
    d = _muat_nd(vc, ITEM_DISKON)["debit_note_data"]
    assert d["item_discount_total"] == Decimal("0") and d["items"][0]["net"] == Decimal("200000")
    assert all(i["discount_amount"] == 0 for i in d["items"]) and _kertas_menjumlah(d)


def test_kontrol_merah_pemeriksa_menjumlah_bisa_gagal():
    # tanpa penyesuaian (neto baris dipakai padahal total mengabaikannya) kertas TIDAK menjumlah
    vc = _vc(tax_amount=Decimal("30030.00"), total_amount=Decimal("303030.00"))
    d = _muat_nd(vc, ITEM_DISKON)["debit_note_data"]
    salah = dict(d, item_discount_total=Decimal("30000"),
                 items=[dict(i, net=i["net"] - j["discount_amount"]) for i, j in zip(d["items"], ITEM_DISKON)])
    assert not _kertas_menjumlah(salah)


def test_render_nd_angka_label_dan_stempel():
    ps = get_pdf_service()
    d = _muat_nd(_vc(), ITEM_DISKON)["debit_note_data"]
    html = ps.render_debit_note(d, TENANT).html
    for teks in ("Nota Debit", "VC-2610-0002", "PI-2610-0001", "Retur barang", "CV Benang Jaya", "Rp 300.000", "-Rp 30.000",
                 "-Rp 27.000", "PPN (11%)", "Rp 26.730", "Total Nota Debit", "Rp 269.730", "Rp 170.000", "Benang putus",
                 "Pengurang hutang ke pemasok", "Kaos Biru Konveksi"):
        assert teks in html, teks
    assert "Nota Kredit" not in html and "pelanggan" not in html and "DRAF" not in html
    assert "DRAF" in ps.render_debit_note(dict(d, status="draft"), TENANT).html
    h_void = ps.render_debit_note(dict(d, status="void", voided_at=datetime(2026, 10, 25, tzinfo=timezone.utc),
                                       voided_reason="salah vendor"), TENANT).html
    assert "DIBATALKAN" in h_void and "salah vendor" in h_void


# ── pemuat: tenant + 404 ────────────────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("muat,baris", [(_muat_nd, lambda: _vc()), (_muat_bayar, lambda: _pay())])
def test_pemuat_404_tenant_lain(muat, baris):
    with pytest.raises(HTTPException) as e:
        muat(baris(), [], tid="t2")
    assert e.value.status_code == 404


def test_pemuat_404_id_rusak():
    for fn in (CD.muat_pdf_debit_note, CP.muat_pdf_payment_out, CB.muat_pdf_bill):
        with pytest.raises(HTTPException) as e:
            asyncio.run(fn(_Conn({}, []), {"tenant_id": "t1"}, "bukan-uuid"))
        assert e.value.status_code == 404


def test_pemuat_setiap_kueri_berfilter_tenant():
    vc = _vc(original_bill_id=uuid.uuid4())
    c = _Conn({"vendor_credits": vc, "bills": {"invoice_number": "PI-9", "issue_date": date(2026, 10, 1)}}, [])
    asyncio.run(CD.muat_pdf_debit_note(c, {"tenant_id": "t1"}, vc["id"]))
    pay = _pay(bank_account_name=None, bank_account_id=uuid.uuid4())
    c2 = _Conn({"bill_payments_v2": pay, "bank_accounts": None}, [])
    asyncio.run(CP.muat_pdf_payment_out(c2, {"tenant_id": "t1"}, pay["id"]))
    lepas = [s for s, _ in c.sql + c2.sql if "tenant_id" not in s and '"Tenant"' not in s and "vendor_credit_items" not in s]
    assert not lepas, lepas  # vendor_credit_items dibaca lewat id ND yang SUDAH lolos filter tenant
