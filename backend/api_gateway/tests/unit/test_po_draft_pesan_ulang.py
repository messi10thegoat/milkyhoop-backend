"""PU-1: Pesan ulang -> PO draf per vendor (preview + buat) memakai jalur PO yang ada; izin = PO·C."""
import asyncio
import inspect
import re
import uuid
from datetime import datetime
from decimal import Decimal as D
from types import SimpleNamespace

import app.middleware.permission_middleware as PM
import app.routers.purchase_orders as PO
from app.schemas.purchase_orders import PoDraftBaris, PoDraftRequest
from app.services import po_draft_pesan_ulang as PDP

T = "kaos-biru-konveksi"
B1, B2, B3 = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
V1, V2 = uuid.uuid4(), uuid.uuid4()


class Kon:
    def __init__(self, barang, terakhir=(), vendor=((V1, "PT Satu"), (V2, "CV Dua"))):
        self.barang, self.terakhir, self.vendor = barang, list(terakhir), vendor

    async def fetch(self, sql, *a):
        if "FROM products p" in sql:
            return self.barang
        if "FROM bill_items" in sql:
            return self.terakhir
        if "FROM vendors" in sql:
            return [{"id": i, "name": n} for i, n in self.vendor if i in a[1]]
        raise AssertionError(sql)

    async def fetchrow(self, sql, *a):
        return {"id": a[0], "name": dict(self.vendor)[a[0]]}

    async def fetchval(self, sql, *a):
        return None


def barang(i, nama, pref=None, harga=0, satuan="pcs"):
    return {"id": i, "item_code": nama[:3], "nama_produk": nama, "satuan": satuan, "preferred_vendor_id": pref, "harga_beli": D(harga)}


def jalan(k, body):
    bl = []
    r = asyncio.run(PDP.rencana(k, T, body, bl))
    return r, bl


def req(items, vendor_id=None):
    return PoDraftRequest(baris=[PoDraftBaris(item_id=str(i), jumlah=q) for i, q in items], vendor_id=vendor_id)


def test_kelompok_per_vendor_utama_dan_terakhir(monkeypatch):
    async def hitung(conn, tid, items, *a, **k):
        baris = [{"subtotal": D(i["unit_price"]) * D(str(i["quantity"]))} for i in items]
        return {"baris": baris, "header": {"total_amount": sum(b["subtotal"] for b in baris)}}
    monkeypatch.setattr(PDP.PR, "hitung_po", hitung)
    k = Kon([barang(B1, "Kain", pref=V1, harga=1000), barang(B2, "Benang", harga=0)],
            terakhir=[{"product_id": B2, "vendor_id": V2, "unit_price": D("2500"), "waktu": datetime(2026, 11, 1)}])
    r, bl = jalan(k, req([(B1, 12), (B2, 4)]))
    assert bl == [] and r["diblok"] == []
    por = {g["vendor_nama"]: g for g in r["kelompok"]}
    assert set(por) == {"PT Satu", "CV Dua"}
    assert por["PT Satu"]["baris"][0]["jumlah_text"] == "12" and por["PT Satu"]["baris"][0]["satuan_text"] == "pcs" and por["PT Satu"]["baris"][0]["harga_text"] == "Rp 1.000"
    assert por["PT Satu"]["total_text"] == "Rp 12.000" and por["CV Dua"]["total_text"] == "Rp 10.000"   # harga tagihan terakhir vendor itu


def test_tanpa_vendor_diblok_berkalimat_dan_vendor_paksa(monkeypatch):
    async def hitung(conn, tid, items, *a, **k):
        return {"baris": [{"subtotal": D(0)} for _ in items], "header": {"total_amount": D(0)}}
    monkeypatch.setattr(PDP.PR, "hitung_po", hitung)
    r, bl = jalan(Kon([barang(B3, "Kancing")]), req([(B3, 5)]))
    assert r["kelompok"] == [] and r["diblok"][0]["alasan_kode"] == "VENDOR_TIDAK_ADA" and "belum ada vendor" in r["diblok"][0]["alasan_text"]
    assert "belum punya vendor" in PDP.teks_ringkas(r)
    r2, bl2 = jalan(Kon([barang(B3, "Kancing")]), req([(B3, 5)], vendor_id=str(V2)))   # vendor dipaksa
    assert [g["vendor_nama"] for g in r2["kelompok"]] == ["CV Dua"] and r2["peringatan"][0]["kode"] == "HARGA_KOSONG"


def test_barang_asing_blok():
    r, bl = jalan(Kon([]), req([(B1, 1)]))
    assert bl and bl[0]["code"] == "BARANG_TIDAK_DITEMUKAN"


def test_rute_memakai_penulis_create_dan_kunci_per_vendor():
    src = inspect.getsource(PO.buat_po_draft)
    assert "create_purchase_order(" in src and "_turunan_idem" in src and "hashlib" in src   # penulis SAMA, bukan salinan; idempotensi per vendor
    paths = {(tuple(sorted(r.methods)), r.path) for r in PO.router.routes if hasattr(r, "methods")}
    assert (("POST",), "/po-draft") in paths and (("POST",), "/po-draft/preview") in paths


def test_izin_po_draft_sama_dengan_buat_po():
    sumber = inspect.getsource(PM)
    assert 'po-draft(/preview)?$", ["POST"], "purchase_order", "C"' in sumber
    assert re.search(r'\^/api/purchase-orders\$", \["POST"\], "purchase_order", "C"', sumber)
