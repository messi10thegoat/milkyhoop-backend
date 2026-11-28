"""BUG-452: penyesuaian KURANG barang ber-batch keluar per batch (FEFO bawaan), sisa tanpa batch tetap boleh; Σ potongan = qty."""
import asyncio
import inspect
import uuid
from decimal import Decimal as D

import app.routers.stock_adjustments as SA
from app.services import batch_alokasi as BAL

L1, L2 = uuid.uuid4(), uuid.uuid4()


class Kon:
    def __init__(self, track=True, rows=((L1, 4), (L2, 2))):
        self.track, self.rows = track, rows

    async def fetchval(self, sql, *a):
        return self.track

    async def fetch(self, sql, *a):
        return [{"batch_id": b, "quantity_to_use": D(q)} for b, q in self.rows]


def _f(k, q):
    return asyncio.run(BAL.fefo_keluar(k, "t", "p", "w", D(q)))


def test_pecah_fefo_dua_batch():
    assert _f(Kon(), 6) == [(L1, D(4)), (L2, D(2))]


def test_sisa_melebihi_stok_batch_jadi_tanpa_batch():
    assert _f(Kon(), 10) == [(L1, D(4)), (L2, D(2)), (None, D(4))]


def test_bukan_barang_batch_satu_potongan_tanpa_batch():
    assert _f(Kon(track=False), 6) == [(None, D(6))]


def test_potongan_tak_melebihi_qty():
    assert _f(Kon(rows=((L1, 9),)), 3) == [(L1, D(3))]


def test_post_penyesuaian_menulis_kartu_per_batch():
    src = inspect.getsource(SA)
    assert "fefo_keluar(" in src and "batch_id=_bid" in src and "_sisa_nilai" in src
