"""GUDANG-NONSTOK (2 Nov 2026; rancangan disetujui MASTER, pola NetSuite: lokasi wajib untuk barang ber-stok saja).

SPEK:
- SATU penentu gudang bawaan (services/gudang_bawaan.bawaan): satu-satunya gudang aktif, atau satu-satunya aktif is_default;
  selain itu None. Dipakai kirim SO (so_pengiriman), SJ dari SO (sj_so), GRN (grn_rencana) -- tak ada lagi salinan
  "ORDER BY is_default DESC ... LIMIT 1" (gudang aktif mana pun) di ketiganya.
- Kirim SO: gudang hanya ditentukan bila ada alokasi baris dilacak stok; semua non-stok -> tanpa gudang, tanpa blok.
- POST /sales-invoices/{id}/fulfill: gudang tak lagi wajib di rute; pagar di INTI _execute_fulfillment: baris ber-stok +
  gudang None -> 400 "Gudang wajib dipilih." (semua pemanggil); baris non-stok tak butuh gudang.
"""
import asyncio
import inspect
import pathlib
import uuid
from decimal import Decimal

import pytest
from fastapi import HTTPException

from app.services import gudang_bawaan as GB
from app.services import so_pengiriman as SP
from app.services import sj_so as SJ
from app.services import grn_rencana as GR
from app.routers import sales_invoices as SI

APP = pathlib.Path(GB.__file__).resolve().parents[1]
T = "tenant-uji"


def _j(c):
    return asyncio.run(c)


def _g(nama, default=False, aktif=True):
    return {"id": uuid.uuid4(), "name": nama, "is_active": aktif, "is_default": default}


class ConnG:
    def __init__(self, gudang):
        self.gudang, self.q = gudang, []

    async def fetch(self, sql, *a):
        self.q.append(" ".join(sql.split()))
        return [g for g in self.gudang if g["is_active"]] if "is_active" in sql else self.gudang


def test_bawaan_satu_aktif_satu_default_atau_none():
    a = _g("A")
    assert _j(GB.bawaan(ConnG([a]), T)) == a
    u, t = _g("Utama", default=True), _g("Toko")
    assert _j(GB.bawaan(ConnG([u, t]), T)) == u  # == data grapgrap/kaos hari ini
    assert _j(GB.bawaan(ConnG([_g("X"), _g("Y")]), T)) is None  # dua aktif tanpa default -> pilih sendiri
    assert _j(GB.bawaan(ConnG([_g("X", default=True), _g("Y", default=True)]), T)) is None
    assert _j(GB.bawaan(ConnG([_g("D", default=True, aktif=False), _g("X"), _g("Y")]), T)) is None  # default nonaktif diabaikan
    assert _j(GB.bawaan(ConnG([]), T)) is None
    c = ConnG([a])
    _j(GB.bawaan(c, T))
    assert "tenant_id = $1" in c.q[0] and "is_active" in c.q[0]


def test_tiga_penentu_memakai_bawaan_tanpa_salinan_longgar():
    for mod, fn in ((SP, SP._gudang), (SJ, SJ._gudang_default), (GR, GR._gudang)):
        src = inspect.getsource(fn)
        assert "gudang_bawaan.bawaan(conn" in src, mod.__name__
        berkas = pathlib.Path(mod.__file__).read_text()
        assert "ORDER BY is_default DESC" not in berkas, mod.__name__


def test_kirim_so_gudang_hanya_bila_ada_baris_dilacak():
    s = inspect.getsource(SP.rencana_kirim_so)
    assert 'if any(a["b"]["dilacak"] for a in alokasi) else (None, None))' in s
    assert '"warehouse_id": gudang["id"] if gudang else None' in s
    assert 'UUID(r["warehouse"]["id"]) if r["warehouse"] else None' in inspect.getsource(SP.tulis_kirim_so)


def test_rute_fulfill_tak_mewajibkan_gudang_di_depan():
    s = inspect.getsource(SI.fulfill_invoice)
    assert 'raise HTTPException(400, "Gudang wajib dipilih.")' not in s
    assert "warehouse_id = UUID(warehouse_id_str) if warehouse_id_str else None" in s
    assert '"warehouse_id": str(warehouse_id) if warehouse_id else None' in s


# ---- pagar inti ----
class ConnInti:
    def __init__(self, dilacak, perlu_kirim):
        self.dilacak, self.perlu_kirim = dilacak, perlu_kirim

    async def fetchrow(self, sql, *a):
        return {"id": a[0], "item_id": uuid.uuid4(), "description": "Kaos", "quantity": Decimal("5"), "fulfilled_qty": Decimal("0"),
                "allocated_amount": Decimal("100"), "recognized_amount": Decimal("0"), "perlu_kirim": self.perlu_kirim}

    async def fetchval(self, sql, *a):
        if "track_inventory" in sql:
            return self.dilacak
        if "warehouse_stock" in sql:
            raise RuntimeError("SAMPAI-CEK-STOK")
        return None

    async def execute(self, sql, *a):
        raise RuntimeError("LEWAT-JALUR-NONSTOK")


def _inti(monkeypatch, dilacak, perlu_kirim, gudang):
    async def tgl(conn, tid):
        from datetime import date
        return date(2026, 11, 2)
    monkeypatch.setattr(SI, "tanggal_dokumen", tgl)
    inv = {"id": uuid.uuid4(), "invoice_number": "INV-1"}
    return _j(SI._execute_fulfillment(ConnInti(dilacak, perlu_kirim), T, None, inv,
                                      [{"invoice_item_id": uuid.uuid4(), "quantity": Decimal("1")}], gudang, None))


def test_inti_baris_berstok_tanpa_gudang_ditolak(monkeypatch):
    with pytest.raises(HTTPException) as e:
        _inti(monkeypatch, True, False, None)
    assert e.value.status_code == 400 and e.value.detail == "Gudang wajib dipilih."
    with pytest.raises(RuntimeError, match="SAMPAI-CEK-STOK"):  # bergudang -> lanjut ke cek stok
        _inti(monkeypatch, True, False, uuid.uuid4())


def test_inti_baris_nonstok_tanpa_gudang_lolos(monkeypatch):
    with pytest.raises(RuntimeError, match="LEWAT-JALUR-NONSTOK"):
        _inti(monkeypatch, False, True, None)
