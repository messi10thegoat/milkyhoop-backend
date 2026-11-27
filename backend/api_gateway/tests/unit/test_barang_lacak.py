"""Form Barang: lacak serial + metode biaya (27 Nov 2026). Menggantikan test_fifo_ditolak: FIFO kini hidup di balik flag fifo_v1."""
import asyncio
import inspect

import pytest
from fastapi import HTTPException

from app.services import barang_lacak as BL
from app.schemas import items as S


class K:
    def __init__(self, fitur=(), riwayat=False):
        self.fitur, self.riwayat = list(fitur), riwayat

    async def fetchval(self, sql, *a):
        assert "inventory_ledger" in sql
        return self.riwayat


def _r(k, **kw):
    import app.services.barang_lacak as m
    async def fa(conn, tid):
        return set(k.fitur)
    m.fitur_aktif = fa
    async def tgl(conn, tid):
        return "2026-11-27"
    import app.utils.tanggal_tenant as tt
    tt.tanggal_dokumen = tgl
    dasar = dict(product_id="p", track_serial=None, costing_method=None, lama={}, ber_stok=True)
    dasar.update(kw)
    return asyncio.run(BL.rencana(k, "t", **dasar))


def _kode(k, **kw):
    with pytest.raises(HTTPException) as e:
        _r(k, **kw)
    return e.value.status_code, e.value.detail["code"]


def test_skema():
    assert "track_serial" in S.CreateItemRequest.model_fields and "track_serial" in S.UpdateItemRequest.model_fields
    assert "track_serial" in S.ItemListItem.model_fields
    assert S.UpdateItemRequest(costing_method="fifo").costing_method == "fifo"   # dinilai server per tenant, bukan skema


def test_serial():
    assert _r(K(["serial_v1"]), track_serial=True) == {"track_serial": True}
    assert _kode(K([]), track_serial=True) == (422, "SERIAL_BELUM_AKTIF")
    assert _kode(K(["serial_v1"]), track_serial=True, ber_stok=False) == (422, "SERIAL_BARANG_NON_STOK")
    assert _kode(K(["serial_v1"], riwayat=True), track_serial=True) == (409, "LACAK_SERIAL_TERKUNCI")
    assert _kode(K([], riwayat=True), track_serial=False, lama={"track_serial": True}) == (409, "LACAK_SERIAL_TERKUNCI")
    assert _r(K([], riwayat=True), track_serial=True, lama={"track_serial": True}) == {}   # tak berubah = bebas


def test_metode():
    assert _r(K(["fifo_v1"]), costing_method="fifo") == {"costing_method": "fifo", "costing_mulai": "2026-11-27"}
    assert _kode(K([]), costing_method="fifo") == (422, "FIFO_BELUM_AKTIF")
    assert _kode(K(["fifo_v1"], riwayat=True), costing_method="fifo") == (409, "METODE_BIAYA_TERKUNCI")
    assert _r(K([]), costing_method="weighted_average") == {}
    assert _r(K([]), costing_method="weighted_average", lama={"costing_method": "fifo"}) == {"costing_method": "weighted_average", "costing_mulai": None}


def test_router_memakai_penentu():
    from app.routers import items as R
    src = inspect.getsource(R)
    assert src.count("barang_lacak.rencana(") == 2 and src.count("barang_lacak.terapkan(") == 2
    assert '"costing_method": "costing_method"' not in src
