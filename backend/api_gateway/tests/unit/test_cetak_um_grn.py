"""PDF Uang Muka Pembelian + Penerimaan Barang (25 Okt 2026, audit simetri Pembelian WORKSPACE): /documents/{vendor_deposit|goods_receipt}/{id}/html|pdf.
Kontrak: pemuat services/cetak_<jenis> (tenant eksplisit, 404, saldo UM dari jurnal) + render (UM = kwitansi.html arah KELUAR, stempel DIBAYAR;
GRN = goods_receipt.html kembaran Surat Jalan, tanpa harga, teks tanpa abu) + routers/dokumen JENIS_CETAK (cetak, BUKAN bagikan) + izin GET R."""
import asyncio
import re
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi import HTTPException

from app.routers import dokumen as DK
from app.services import cetak_goods_receipt as CG
from app.services import cetak_vendor_deposit as CV
from app.services import uang_muka_vendor_daftar as UMD
from app.services.pdf_service import PDFService

APP = Path(__file__).resolve().parents[2] / "app"
TID = "tenant-uji"
KOP = {"display_name": "Usaha Uji", "address": "Jl. Pabrik 2", "phone": "0812", "logo_url": None, "email": None}


def _jalan(c):
    return asyncio.run(c)


# ------------------------------------------------------------------ rute, izin
@pytest.mark.parametrize("jenis", ["vendor_deposit", "goods_receipt"])
def test_jenis_cetak_bukan_jenis_bagikan(jenis):
    assert jenis in DK.JENIS_CETAK
    assert jenis not in DK.JENIS  # bagikan butuh kind di CHECK document_shares (V379)


@pytest.mark.parametrize("jenis,pemuat,render", [
    ("vendor_deposit", "cetak_vendor_deposit import muat_pdf_vendor_deposit", "ps.render_receipt("),
    ("goods_receipt", "cetak_goods_receipt import muat_pdf_goods_receipt", "ps.render_goods_receipt("),
])
def test_render_memanggil_pemuat_dan_render_sendiri(jenis, pemuat, render):
    src = (APP / "routers" / "dokumen.py").read_text()
    badan = src.split("async def _render(", 1)[1].split("\ndef ", 1)[0]
    blok = badan.split(f'if jenis == "{jenis}":', 1)[1].split("\n    if jenis ==", 1)[0].split("\n    raise", 1)[0]
    assert pemuat in blok and render in blok


@pytest.mark.parametrize("jenis,modul", [("vendor_deposit", "vendor_deposit"), ("goods_receipt", "purchase_order")])
def test_izin_cetak(jenis, modul):
    src = (APP / "middleware" / "permission_middleware.py").read_text()
    assert f'(r"^/api/documents/{jenis}/[^/]+/(html|pdf)$", ["GET"], "{modul}", "R"),' in src
    pola = re.compile(rf"^/api/documents/{jenis}/[^/]+/(html|pdf)$")
    assert pola.match(f"/api/documents/{jenis}/abc/pdf") and not pola.match(f"/api/documents/{jenis}/abc/share")


# ------------------------------------------------------------------ pemuat uang muka
class _ConnVD:
    def __init__(self, vd):
        self.vd, self.sql = vd, []

    async def fetchrow(self, sql, *a):
        self.sql.append((sql, a))
        if "FROM vendor_deposits" in sql:
            return self.vd if self.vd and a[1] == TID else None
        if '"Tenant"' in sql:
            assert a[0] == TID
            return KOP
        raise AssertionError(sql)


def _vd(**k):
    d = {"id": uuid.uuid4(), "deposit_number": "VD-2610-0001", "deposit_date": date(2026, 10, 25), "status": "posted",
         "amount": Decimal("1500000.00"), "payment_method": "bank_transfer", "reference": "TRF-77", "notes": "DP kain",
         "vendor_name": "PT Kain Jaya", "po_number": "PO-2610-0003", "bank_account_name": "BCA Operasional", "bank_name": "BCA",
         "reversed_at": None, "remaining_amount": Decimal("999"), "applied_amount": Decimal("0")}
    d.update(k)
    return d


@pytest.fixture
def sisa_jurnal(monkeypatch):
    dipanggil = {}

    async def akun(conn, tid):
        assert tid == TID
        return "akun-ap-prepaid"

    async def fakta(conn, tid, akun_id, ids):
        dipanggil["ids"] = ids
        assert tid == TID and akun_id == "akun-ap-prepaid"
        return {ids[0]: {"sisa": dipanggil.get("sisa", Decimal("1000000.00"))}}

    monkeypatch.setattr(UMD, "akun_baca", akun)
    monkeypatch.setattr(UMD, "fakta_vd", fakta)
    return dipanggil


def test_vd_404_tenant_lain(sisa_jurnal):
    with pytest.raises(HTTPException) as e:
        _jalan(CV.muat_pdf_vendor_deposit(_ConnVD(None), {"tenant_id": TID}, uuid.uuid4()))
    assert e.value.status_code == 404


def test_vd_konteks_arah_keluar_dan_saldo_dari_jurnal(sisa_jurnal):
    vd = _vd()
    c = _ConnVD(vd)
    m = _jalan(CV.muat_pdf_vendor_deposit(c, {"tenant_id": TID}, vd["id"]))
    r = m["receipt_data"]
    assert r["title"] == "Bukti Kas Keluar" and r["subtitle"] == "Uang muka pembelian ke pemasok" and r["party_label"] == "Dibayarkan Kepada"
    assert r["hide_stamp"] is True and "stamp_label" not in r
    assert r["payer_name"] == "PT Kain Jaya" and r["receipt_number"] == "VD-2610-0001" and r["receipt_date"] == "2026-10-25"
    assert r["purpose_ref"] == "PO-2610-0003" and r["source_ref"] == "TRF-77"
    assert r["method"] == "Transfer Bank" and r["bank_name"] == "BCA Operasional"
    assert r["remaining"] == Decimal("1000000.00")  # jurnal, BUKAN cache remaining_amount (999)
    assert sisa_jurnal["ids"] == [vd["id"]]
    assert "satu juta lima ratus ribu" in r["amount_words"].lower()
    assert m["tenant_info"]["name"] == "Usaha Uji"
    sql = c.sql[0][0]
    for join in ("v.tenant_id = vd.tenant_id", "po.tenant_id = vd.tenant_id", "ba.tenant_id = vd.tenant_id", "je.tenant_id = vd.tenant_id"):
        assert join in sql


def test_vd_draf_tanpa_saldo_tunai_tanpa_po(sisa_jurnal):
    sisa_jurnal["sisa"] = None  # fakta_vd: draf/void -> sisa None
    vd = _vd(status="draft", payment_method="cash", po_number=None, reference=None, bank_account_name=None, bank_name=None)
    r = _jalan(CV.muat_pdf_vendor_deposit(_ConnVD(vd), {"tenant_id": TID}, vd["id"]))["receipt_data"]
    assert r["remaining"] is None and r["purpose_ref"] is None and r["source_ref"] is None and r["method"] == "Tunai"


def test_vd_batal_tanggal_dari_jurnal_pembalik(sisa_jurnal):
    t = datetime(2026, 10, 25, 3, 0, tzinfo=timezone.utc)
    vd = _vd(status="void", reversed_at=t)
    assert _jalan(CV.muat_pdf_vendor_deposit(_ConnVD(vd), {"tenant_id": TID}, vd["id"]))["receipt_data"]["voided_at"] == t
    vd = _vd(status="posted", reversed_at=t)  # hidup: reversed_at (data aneh) tak memicu tanda batal
    assert _jalan(CV.muat_pdf_vendor_deposit(_ConnVD(vd), {"tenant_id": TID}, vd["id"]))["receipt_data"]["voided_at"] is None


# ------------------------------------------------------------------ pemuat GRN
class _ConnGR:
    def __init__(self, gr, vendor=None, items=()):
        self.gr, self.vendor, self.items, self.sql = gr, vendor, list(items), []

    async def fetchrow(self, sql, *a):
        self.sql.append((sql, a))
        if "FROM goods_receipts" in sql:
            return self.gr if self.gr and a[1] == TID else None
        if "FROM vendors" in sql:
            assert a[1] == TID
            return self.vendor
        if '"Tenant"' in sql:
            return KOP
        raise AssertionError(sql)

    async def fetch(self, sql, *a):
        self.sql.append((sql, a))
        assert "FROM goods_receipt_items" in sql and a == (self.gr["id"],)
        return self.items


def _gr(**k):
    d = {"id": uuid.uuid4(), "receipt_number": "GRN-2610-0002", "receipt_date": date(2026, 10, 24), "status": "posted", "notes": None,
         "voided_at": None, "voided_reason": None, "vendor_id": uuid.uuid4(), "po_number": "PO-2610-0003", "po_ref_no": None,
         "vendor_name": "PT Kain Jaya", "warehouse_name": "Gudang Utama"}
    d.update(k)
    return d


def _brs(desc, qty, ret=0, unit="m", kode=None):
    return {"description": desc, "item_code": kode, "quantity": Decimal(qty), "quantity_returned": Decimal(ret), "unit": unit}


def test_gr_404_tenant_lain():
    with pytest.raises(HTTPException) as e:
        _jalan(CG.muat_pdf_goods_receipt(_ConnGR(None), {"tenant_id": TID}, uuid.uuid4()))
    assert e.value.status_code == 404


def test_gr_konteks():
    gr = _gr()
    v = {"address": "Jl. Tekstil 5", "city": "Bandung", "province": None, "postal_code": None, "phone": None, "mobile_phone": "0813"}
    c = _ConnGR(gr, v, [_brs("Kain katun", "20", kode="KT-1"), _brs("Kancing", "100", ret="5", unit="pcs")])
    g = _jalan(CG.muat_pdf_goods_receipt(c, {"tenant_id": TID}, gr["id"]))["gr_data"]
    assert g["receipt_number"] == "GRN-2610-0002" and g["po_number"] == "PO-2610-0003" and g["warehouse_name"] == "Gudang Utama"
    assert g["vendor_alamat"] == "Jl. Tekstil 5\nBandung" and g["vendor_telepon"] == "0813"
    assert [b["description"] for b in g["items"]] == ["Kain katun", "Kancing"] and g["ada_retur"] is True
    tanpa = _ConnGR(gr, None, [_brs("Kain katun", "20")])
    g2 = _jalan(CG.muat_pdf_goods_receipt(tanpa, {"tenant_id": TID}, gr["id"]))["gr_data"]
    assert g2["ada_retur"] is False and g2["vendor_alamat"] is None and g2["vendor_telepon"] is None  # kolom Qty Diretur hanya bila ada retur
    sql = c.sql[0][0]
    for join in ("po.tenant_id = g.tenant_id", "v.tenant_id = g.tenant_id", "w.tenant_id = g.tenant_id", "g.tenant_id = $2"):
        assert join in sql


# ------------------------------------------------------------------ render
def _ps():
    return PDFService()


TENANT = {"name": "Usaha Uji", "address": "Jl. Pabrik 2", "phone": "0812", "logo_data": None}


def _rd(**k):
    d = {**CV.TEKS, "status": "posted", "voided_at": None, "voided_reason": None, "receipt_number": "VD-2610-0001",
         "receipt_date": "2026-10-25", "payer_name": "PT Kain Jaya", "amount": Decimal("1500000.00"),
         "amount_words": "satu juta lima ratus ribu rupiah", "method": "Transfer Bank", "bank_name": "BCA Operasional",
         "purpose_ref": "PO-2610-0003", "source_ref": "TRF-77", "remaining": Decimal("1000000.00"), "notes": None}
    d.update(k)
    return d


def test_render_vd_arah_keluar():
    h = _ps().render_receipt(_rd(), TENANT).html
    for teks in ("Bukti Kas Keluar", "Uang muka pembelian ke pemasok", "Dibayarkan Kepada", "Jumlah Dibayar", "Untuk Pesanan Pembelian",
                 "PO-2610-0003", "Referensi", "TRF-77", "Saldo Uang Muka", "No. Uang Muka", "Pembayar,"):
        assert teks in h, teks
    for teks in ("DITERIMA", "DIBAYAR", "kw-stempel-diterima", "Bukti Pembayaran Uang Muka", "Telah Terima Dari", "Jumlah Diterima",
                 "Penerima,"):
        assert teks not in h, teks


def test_render_vd_draf_tanpa_stempel_batal_bertanda():
    hd = _ps().render_receipt(_rd(status="draft", remaining=None), TENANT).html
    assert "DIBAYAR" not in hd and "DITERIMA" not in hd and "kw-stempel-diterima" not in hd
    h = _ps().render_receipt(_rd(status="void", remaining=None, voided_at=datetime(2026, 10, 25, 3, tzinfo=timezone.utc)), TENANT).html
    assert "DIBATALKAN" in h and "kw-stempel-diterima" not in h


def _gd(**k):
    d = {"id": "x", "receipt_number": "GRN-2610-0002", "receipt_date": "2026-10-24", "status": "posted", "notes": "Kardus penyok 1",
         "voided_at": None, "voided_reason": None, "po_number": "PO-2610-0003", "po_ref_no": None, "warehouse_name": "Gudang Utama",
         "vendor_name": "PT Kain Jaya", "vendor_alamat": "Jl. Tekstil 5\nBandung", "vendor_telepon": "0813",
         "items": [_brs("Kain katun", "20", kode="KT-1")], "ada_retur": False}
    d.update(k)
    return d


def test_render_gr_isi_tanpa_harga_tanpa_abu():
    r = _ps().render_goods_receipt(_gd(), TENANT)
    h = r.html
    for teks in ("PENERIMAAN BARANG", "GRN-2610-0002", "PO-2610-0003", "Gudang Utama", "PT Kain Jaya", "Kain katun", "Kode: KT-1",
                 "Kardus penyok 1", "Diserahkan oleh,", "Diterima oleh,", "Usaha Uji"):
        assert teks in h, teks
    assert "Qty Diretur" not in h and "Rp" not in h and "DIBATALKAN" not in h
    for abu in ("#666", "#777", "#888", "#999", "#aaa", "#555", "#444"):
        assert abu not in h.lower(), abu
    assert "invoice.css" in r.css and r.font


def test_render_gr_retur_dan_batal():
    h = _ps().render_goods_receipt(_gd(items=[_brs("Kancing", "100", ret="5", unit="pcs")], ada_retur=True), TENANT).html
    assert "Qty Diretur" in h
    h = _ps().render_goods_receipt(_gd(status="void", voided_reason="Salah PO",
                                       voided_at=datetime(2026, 10, 25, 3, tzinfo=timezone.utc)), TENANT).html
    assert "DIBATALKAN" in h and "Salah PO" in h
