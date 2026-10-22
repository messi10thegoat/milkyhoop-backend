"""P-U2 sisi TULIS Pesanan Pembelian (22 Okt 2026): uang Decimal (po_hitung), idempotensi, vendor milik tenant, lock+FOR UPDATE,
total dihitung kode (trigger di-DROP V435), tautan bill_items (V436) + sinkron_tagih, teks Indonesia."""
import asyncio
import inspect
import os
import re
import uuid
from datetime import date
from decimal import Decimal
from pathlib import Path

os.environ.setdefault("OPENAI_API_KEY", "sk-boneka-unit-test-tanpa-jaringan")

import pytest  # noqa: E402
from fastapi import HTTPException  # noqa: E402

from app.routers import purchase_orders as PO  # noqa: E402
from app.schemas import purchase_orders as PS  # noqa: E402
from app.services import po_daftar as PD, po_hitung as PH  # noqa: E402

T = "kaos-biru-konveksi"
PID = uuid.uuid4()
VID = uuid.uuid4()
D = Decimal


# ------------------------------------------------------------------ po_hitung (murni)
def test_baris_half_up_dan_sen_terjaga():
    b = PH.hitung_baris({"quantity": "3", "unit_price": "33.335", "discount_percent": 0, "discount_amount": 0, "tax_rate": 11})
    assert b["subtotal"] == D("100.01") and b["tax_amount"] == D("11.00") and b["total"] == D("111.01")   # 100.005 -> 100.01 (HALF_UP)
    b = PH.hitung_baris({"quantity": "2.5", "unit_price": "1000.50", "discount_percent": 10, "tax_rate": 0})
    assert b["subtotal"] == D("2501.25") and b["discount_amount"] == D("250.13") and b["total"] == D("2251.12")


def test_baris_diskon_nominal_dibatasi_subtotal():
    b = PH.hitung_baris({"quantity": 1, "unit_price": 100, "discount_amount": 500, "tax_rate": 11})
    assert b["discount_amount"] == D("100.00") and b["total"] == D("0.00") and b["tax_amount"] == D("0.00")


def test_header_diskon_pajak_header_tersimpan_bukan_ditimpa():
    baris = [PH.hitung_baris({"quantity": 10, "unit_price": 1000, "discount_percent": 0, "tax_rate": 0}),
             PH.hitung_baris({"quantity": 5, "unit_price": 2000, "discount_percent": 10, "tax_rate": 0})]       # 10000 + (10000-1000)
    h = PH.hitung_header(baris, discount_percent=10, discount_amount=0, tax_rate=11)
    assert h["subtotal"] == D("19000.00") and h["discount_amount"] == D("1900.00")
    assert h["tax_amount"] == D("1881.00") and h["total_amount"] == D("19000.00") - D("1900.00") + D("1881.00")
    # trigger lama: total = Σ total baris = 19000 -> diskon & pajak header HILANG; kini tersimpan
    assert h["total_amount"] != sum(b["total"] for b in baris)


def test_header_pajak_baris_diskalakan_diskon_header():
    baris = [PH.hitung_baris({"quantity": 1, "unit_price": 1000, "tax_rate": 11})]            # pajak baris 110
    h = PH.hitung_header(baris, discount_percent=50, discount_amount=0, tax_rate=0)
    assert h["subtotal"] == D("1000.00") and h["discount_amount"] == D("500.00") and h["tax_amount"] == D("55.00") and h["total_amount"] == D("555.00")


def test_header_tanpa_diskon_pajak_sama_dengan_jumlah_baris():
    baris = [PH.hitung_baris({"quantity": 3, "unit_price": "10.10", "tax_rate": 11}), PH.hitung_baris({"quantity": 1, "unit_price": "5.55", "tax_rate": 11})]
    h = PH.hitung_header(baris)
    assert h["total_amount"] == sum(b["total"] for b in baris) and h["subtotal"] == sum(b["subtotal"] for b in baris)


# ------------------------------------------------------------------ fake koneksi
class _Tx:
    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False


class _Req:
    class state:
        user = {"tenant_id": T, "user_id": str(uuid.uuid4())}


class Kon:
    def __init__(self, po=None, vendor=True, items=None):
        self.po, self.vendor, self.items = po, vendor, items or []
        self.log, self.writes = [], []

    def transaction(self): return _Tx()

    async def execute(self, sql, *a):
        q = " ".join(sql.split())
        self.log.append(("exec", q[:100]))
        if q.startswith(("INSERT", "UPDATE", "DELETE")):
            self.writes.append((q[:60], a))
        return "OK"

    async def fetchrow(self, sql, *a):
        q = " ".join(sql.split())
        self.log.append(("fetchrow", q[:100]))
        if "FROM vendors" in q: return {"id": VID, "name": "Vendor X"} if self.vendor else None
        if "SELECT discount_percent, discount_amount, tax_rate FROM purchase_orders" in q:
            return {"discount_percent": D("10"), "discount_amount": D("0"), "tax_rate": D("0")}
        if "FROM purchase_orders" in q: return self.po
        return None

    async def fetch(self, sql, *a):
        q = " ".join(sql.split())
        self.log.append(("fetch", q[:100]))
        if "FROM purchase_order_items" in q: return self.items
        return []

    async def fetchval(self, sql, *a):
        q = " ".join(sql.split())
        self.log.append(("fetchval", q[:100]))
        if "generate_purchase_order_number" in q: return "PO-2610-0001"
        if q.startswith("INSERT INTO purchase_orders"):
            self.writes.append((q[:60], a))
            return PID
        return None


def _pool(monkeypatch, kon):
    class CM:
        async def __aenter__(s): return kon
        async def __aexit__(s, *a): return False
    class P:
        def acquire(s): return CM()
    async def gp(): return P()
    monkeypatch.setattr(PO, "get_pool", gp)


def _body(**kw):
    d = dict(vendor_id=str(VID), vendor_name="Vendor X", po_date=date(2026, 10, 22),
             items=[PS.PurchaseOrderItemCreate(description="Kain", quantity=D("3"), unit_price=D("33.34"), tax_rate=11)],
             discount_percent=10, tax_rate=0)
    d.update(kw)
    return PS.CreatePurchaseOrderRequest(**d)


# ------------------------------------------------------------------ create
def test_skema_terima_sen_dan_ditolak_lebih_dari_2dp():
    assert PS.PurchaseOrderItemCreate(description="x", quantity=1, unit_price="1000.50").unit_price == D("1000.50")
    with pytest.raises(Exception):
        PS.PurchaseOrderItemCreate(description="x", quantity=1, unit_price="1.005")


def test_create_vendor_bukan_milik_tenant_422_berkode(monkeypatch):
    kon = Kon(vendor=False)
    _pool(monkeypatch, kon)
    with pytest.raises(HTTPException) as e:
        asyncio.run(PO.create_purchase_order(_Req(), _body()))
    assert e.value.status_code == 422 and e.value.detail["code"] == "VENDOR_TIDAK_DITEMUKAN"
    assert not any("INSERT INTO purchase_orders" in q for k, q in kon.log)


def test_create_menyimpan_total_dari_baris_dengan_diskon_header(monkeypatch):
    kon = Kon()
    _pool(monkeypatch, kon)
    r = asyncio.run(PO.create_purchase_order(_Req(), _body()))
    assert r["success"] is True and r["data"]["po_number"] == "PO-2610-0001"
    ins = next(a for q, a in kon.writes if q.startswith("INSERT INTO purchase_orders"))
    # a = tenant, nomor, vendor_id, vendor_name, subtotal, disc%, disc_amt, rate, tax, total, ...
    assert ins[4] == D("100.02") and ins[9] == ins[4] - ins[6] + ins[8]
    assert any(q.startswith("UPDATE purchase_orders SET subtotal") for q, a in kon.writes)    # total dihitung ulang dari baris DB (satu jalur)
    baris = [a for q, a in kon.writes if q.startswith("INSERT INTO purchase_order_items")]
    assert len(baris) == 1 and baris[0][6] == D("33.34") and baris[0][12] == D("100.02")


def test_create_tanpa_vendor_id_memakai_nama_saja(monkeypatch):
    kon = Kon(vendor=False)
    _pool(monkeypatch, kon)
    r = asyncio.run(PO.create_purchase_order(_Req(), _body(vendor_id=None)))
    assert r["success"] is True and not any("FROM vendors" in q for k, q in kon.log)


# ------------------------------------------------------------------ update / delete / send / cancel / close
def _po(status="draft"): return {"id": PID, "status": status, "po_number": "PO-1"}


def _urut_kunci_sebelum_baca(kon):
    i_lock = next(i for i, (k, q) in enumerate(kon.log) if k == "exec" and "pg_advisory_xact_lock" in q)
    i_baca = next(i for i, (k, q) in enumerate(kon.log) if k == "fetchrow" and "FROM purchase_orders" in q and "FOR UPDATE" in q)
    return i_lock, i_baca


@pytest.mark.parametrize("fn,args,status", [
    ("update_purchase_order", (PS.UpdatePurchaseOrderRequest(notes="x"),), "draft"),
    ("delete_purchase_order", (), "draft"),
    ("send_purchase_order", (), "draft"),
    ("cancel_purchase_order", (PS.CancelPurchaseOrderRequest(reason="salah"),), "sent"),
    ("close_purchase_order", (), "received"),
])
def test_tulis_kunci_dulu_lalu_baca_for_update(monkeypatch, fn, args, status):
    kon = Kon(po=_po(status), items=[])
    async def _f(*a, **k): return None
    _pool(monkeypatch, kon)
    # cancel menghitung qty dari baris: tiruan fetchrow mengembalikan self.po untuk FROM purchase_orders; qty lewat fetchrow lain
    orig = kon.fetchrow
    async def fr(sql, *a):
        if "SUM(quantity_received)" in " ".join(sql.split()): return {"diterima": D("0"), "ditagih": D("0")}
        return await orig(sql, *a)
    kon.fetchrow = fr
    r = asyncio.run(getattr(PO, fn)(_Req(), PID, *args))
    assert r["success"] is True
    i_lock, i_baca = _urut_kunci_sebelum_baca(kon)
    assert i_lock < i_baca


@pytest.mark.parametrize("fn,args,status,potongan", [
    ("update_purchase_order", (PS.UpdatePurchaseOrderRequest(notes="x"),), "received", "tidak bisa diubah"),
    ("delete_purchase_order", (), "sent", "Hanya pesanan pembelian berstatus Draf"),
    ("send_purchase_order", (), "sent", "tidak bisa dikirim"),
    ("close_purchase_order", (), "draft", "tidak bisa ditutup"),
    ("cancel_purchase_order", (PS.CancelPurchaseOrderRequest(reason="x"),), "closed", "tidak bisa dibatalkan"),
])
def test_status_salah_ditolak_indonesia_di_dalam_lock(monkeypatch, fn, args, status, potongan):
    kon = Kon(po=_po(status))
    _pool(monkeypatch, kon)
    with pytest.raises(HTTPException) as e:
        asyncio.run(getattr(PO, fn)(_Req(), PID, *args))
    assert e.value.status_code == 400 and potongan in e.value.detail
    assert not any(q.startswith(("UPDATE purchase_orders SET status", "DELETE FROM purchase_orders")) for q, a in kon.writes)


@pytest.mark.parametrize("diterima,ditagih,potongan", [("2", "0", "barangnya sudah diterima"), ("0", "1", "sudah ditagih")])
def test_cancel_diukur_dari_baris_bukan_kolom_cache(monkeypatch, diterima, ditagih, potongan):
    kon = Kon(po=_po("sent"))
    _pool(monkeypatch, kon)
    orig = kon.fetchrow
    async def fr(sql, *a):
        if "SUM(quantity_received)" in " ".join(sql.split()): return {"diterima": D(diterima), "ditagih": D(ditagih)}
        return await orig(sql, *a)
    kon.fetchrow = fr
    with pytest.raises(HTTPException) as e:
        asyncio.run(PO.cancel_purchase_order(_Req(), PID, PS.CancelPurchaseOrderRequest(reason="x")))
    assert potongan in e.value.detail


def test_update_diskon_header_saja_menghitung_ulang_total(monkeypatch):
    kon = Kon(po=_po("draft"), items=[{"subtotal": D("1000"), "discount_amount": D("0"), "tax_amount": D("110")}])
    _pool(monkeypatch, kon)
    asyncio.run(PO.update_purchase_order(_Req(), PID, PS.UpdatePurchaseOrderRequest(discount_percent=10)))
    tot = next(a for q, a in kon.writes if q.startswith("UPDATE purchase_orders SET subtotal"))
    # po stub: diskon 10%, tarif 0 -> neto 1000, diskon 100, pajak baris 110 x 900/1000 = 99.00, total 999.00
    assert tot[1:5] == (D("1000.00"), D("100.00"), D("99.00"), D("999.00"))


def test_update_tanpa_perubahan_uang_tidak_menghitung_ulang(monkeypatch):
    kon = Kon(po=_po("draft"))
    _pool(monkeypatch, kon)
    asyncio.run(PO.update_purchase_order(_Req(), PID, PS.UpdatePurchaseOrderRequest(notes="catatan")))
    assert not any(q.startswith("UPDATE purchase_orders SET subtotal") for q, a in kon.writes)


# ------------------------------------------------------------------ to-bill + sinkron_tagih
def test_tobill_kunci_for_update_dan_tautan_baris_po():
    src = inspect.getsource(PO.convert_to_bill)
    assert "_kunci_po(conn, ctx[\"tenant_id\"], po_id)" in src and src.count("FOR UPDATE") >= 2
    assert "purchase_order_item_id" in src and "PD.sinkron_tagih(conn" in src
    assert "UPDATE purchase_order_items\n                        SET quantity_billed" not in src         # tak ada lagi penambahan kumulatif ditulis tangan
    assert 'PO_TOBILL' in src and 'mulai_aksi' in src


class KonSink:
    def __init__(self, status, baris):
        self.status, self.baris, self.writes = status, baris, []
    async def fetchrow(self, sql, *a): return {"id": PID, "status": self.status}
    async def fetch(self, sql, *a): return self.baris
    async def execute(self, sql, *a):
        q = " ".join(sql.split())
        if q.startswith("UPDATE purchase_orders"): self.writes.append(a)
        return "OK"


def _brs(*b): return [{"quantity": D(q), "quantity_received": D(r), "quantity_billed": D(t), "total": D("1000")} for q, r, t in b]


@pytest.mark.parametrize("awal,baris,harap", [
    ("sent", _brs(("10", "0", "10")), "billed"),                    # tertagih penuh
    ("sent", _brs(("10", "0", "4")), "partial_billed"),
    ("partial_billed", _brs(("10", "10", "0")), "received"),         # bill dihapus/void -> kembali ke diterima penuh
    ("partial_billed", _brs(("10", "4", "0")), "partial_received"),
    ("billed", _brs(("10", "0", "0")), "sent"),                      # semua bill void, belum diterima
    ("closed", _brs(("10", "10", "0")), "closed"),                   # closed/cancelled/draft tak disentuh
    ("cancelled", _brs(("10", "0", "0")), "cancelled"),
    ("draft", _brs(("10", "0", "0")), "draft"),
])
def test_sinkron_tagih_status_turunan(awal, baris, harap):
    kon = KonSink(awal, baris)
    h = asyncio.run(PD.sinkron_tagih(kon, T, PID))
    assert h["status"] == harap and kon.writes[-1][2] == harap


def test_sinkron_tagih_jumlah_half_up():
    kon = KonSink("sent", [{"quantity": D("3"), "quantity_received": D("0"), "quantity_billed": D("1"), "total": D("100.00")}])
    h = asyncio.run(PD.sinkron_tagih(kon, T, PID))
    assert h["amount_billed"] == D("33.33")


# ------------------------------------------------------------------ teks Indonesia + migrasi
def test_router_po_tanpa_galat_inggris():
    src = inspect.getsource(PO)
    for kata in ("Purchase order not found", "Failed to", "Cannot ", "Only draft POs", "No unbilled items", "PO item {", "Bill quantity"):
        assert kata not in src, kata


def test_idempotensi_create_terdaftar_sumber_buat():
    from app.utils import idempotency as I
    from app.services import idem_buat
    assert I.SUMBER_BUAT["PO_CREATE"] == ("purchase_orders", None, ())
    assert idem_buat._TABEL["purchase_orders"] == "po_number"
