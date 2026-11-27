"""Celah 8: medan dalam_perjalanan (irisan Transit) di laporan nilai/mutasi & kartu stok."""
import asyncio
from datetime import date
from decimal import Decimal

from app.services import transit_baca as TB
from app.services import laporan_nilai_persediaan as LNP


def test_bentuk_kosong_none():
    assert TB.bentuk(0, 0, 0) is None


def test_bentuk_isi():
    b = TB.bentuk(Decimal("12"), Decimal("120000"), 0)
    assert b["qty"] == 12.0 and b["nilai"] == 120000.0 and b["teks"].startswith("Dalam perjalanan: ")
    assert b["qty_text"] in b["teks"] and b["nilai_text"] in b["teks"]


class Kon:
    """Fake: mengenali tiap query laporan nilai + query Transit."""
    async def fetch(self, sql, *a):
        if sql == TB.SQL_NILAI:
            return [{"product_id": "p1", "qty": Decimal("3")}, {"product_id": "p2", "qty": Decimal("2")}]
        if sql == LNP.SQL_WAC:
            return [{"product_id": "p1", "average_cost": Decimal("1000")}, {"product_id": "p2", "average_cost": Decimal("500")}]
        if sql == LNP.SQL_QTY_SEMUA:
            return [{"product_id": "p1", "qty": Decimal("10")}]
        if sql in (LNP.SQL_QTY_BARANG, LNP.SQL_QTY_GUDANG):
            return []
        return []

    async def fetchval(self, *a):
        return 0


def _muat(**kw):
    return asyncio.run(LNP.muat_nilai_persediaan(Kon(), "t", date(2026, 11, 27), 0, **kw))


def test_nilai_transit_pakai_hpp_laporan():
    d = _muat()
    assert d["dalam_perjalanan"]["qty"] == 5.0 and d["dalam_perjalanan"]["nilai"] == 4000.0   # 3×1000 + 2×500


def test_nilai_transit_none_bila_disaring_gudang():
    assert _muat(gudang_id="11111111-1111-1111-1111-111111111111")["dalam_perjalanan"] is None
