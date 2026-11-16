"""BUG-253 (16 Nov 2026): diskon % per baris daftar harga -- resolve `diskon_persen` dari baris YANG SAMA dengan harga (tingkat qty + berjangka ikut pilih_baris)."""
import asyncio
from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest

from app.services import harga_jual as HJ
from app.services import satuan_dasar as SD

PID, DAFTAR, TID = uuid4(), uuid4(), "t-1"


def r(harga, diskon, qty=1, mulai=None, akhir=None, unit=None, minimum=None):
    return {"item_id": PID, "unit": unit, "price": Decimal(harga), "min_price": minimum, "discount_percent": Decimal(str(diskon)) if diskon is not None else None,
            "min_quantity": Decimal(qty), "start_date": mulai, "end_date": akhir}


class Konn:
    def __init__(self, rows):
        self.rows = rows

    async def fetch(self, sql, *a):
        if "FROM price_list_items" in sql:
            return self.rows
        if "FROM products" in sql or "unit_conversions" in sql:
            return []
        if "inventory" in sql.lower() or "harga_jual" in sql.lower():
            return []
        raise AssertionError(sql)


@pytest.fixture(autouse=True)
def stub(monkeypatch):
    async def daftar(conn, tid, customer_id, tanggal):
        return {"id": DAFTAR, "code": "D", "name": "D", "asal": "pelanggan"}

    async def muat(conn, tid, ids):
        return {"alias": {}, "barang": {str(PID): {"nama": "Minyak", "dasar": "Pcs", "konversi": {}}}}

    async def dasar(conn, tid, ids):
        return {}
    monkeypatch.setattr(HJ, "daftar_untuk", daftar)
    monkeypatch.setattr(SD, "muat", muat)


def hasil(rows, qty=1, tanggal=date(2026, 11, 20)):
    """Pilih baris persis seperti resolve: filter berlaku + min qty lalu pilih_baris."""
    cocok = [x for x in rows if HJ._berlaku(x, tanggal) and Decimal(str(x["min_quantity"])) <= qty]
    return HJ.pilih_baris(cocok) if cocok else None


def test_diskon_mengikuti_tingkat_qty_baris_yang_sama_dengan_harga():
    rows = [r(10000, 0, qty=1), r(10000, 5, qty=10), r(10000, 8, qty=50)]
    assert [hasil(rows, q)["discount_percent"] for q in (1, 9, 10, 49, 50, 500)] == [0, 0, 5, 5, 8, 8]


def test_diskon_ikut_berjangka_mulai_terbaru_sampai_tanggal_dokumen():
    rows = [r(10000, 2), r(10000, 6, mulai=date(2026, 11, 20)), r(10000, 9, mulai=date(2026, 11, 25))]
    assert [hasil(rows, 1, date(2026, 11, d))["discount_percent"] for d in (16, 20, 24, 25, 30)] == [2, 6, 6, 9, 9]


def test_resolve_memberi_diskon_persen_float_null_bila_tak_diatur_dan_bukan_nol():
    async def jalan(rows):
        return await HJ.harga_saran(Konn(rows), TID, None, date(2026, 11, 20), [{"key": "k", "item_id": str(PID), "unit": None, "quantity": 12}])
    a = asyncio.run(jalan([r(10000, 5, qty=10), r(10000, None, qty=1)]))["lines"][0]
    assert a["sumber"] == "daftar" and a["diskon_persen"] == 5.0 and isinstance(a["diskon_persen"], float) and a["harga"] == 10000.0
    b = asyncio.run(jalan([r(10000, None)]))["lines"][0]
    assert b["diskon_persen"] is None                                           # tak diatur != 0%
    c = asyncio.run(jalan([r(10000, 0)]))["lines"][0]
    assert c["diskon_persen"] == 0.0                                            # 0% eksplisit tetap 0.0
    d = asyncio.run(jalan([]))["lines"][0]
    assert d["diskon_persen"] is None and d["sumber"] != "daftar"               # bukan dari daftar -> tak ada diskon


def test_diskon_persen_tak_mengubah_harga_atau_lantai():
    rows = [r(10000, 5, minimum=Decimal(9000))]
    x = asyncio.run(HJ.harga_saran(Konn(rows), TID, None, date(2026, 11, 20), [{"key": "k", "item_id": str(PID), "unit": None, "quantity": 1}]))["lines"][0]
    assert x["harga"] == 10000.0 and x["harga_minimum"] == 9000.0 and x["diskon_persen"] == 5.0                 # harga TIDAK dipotong server; FE mengisi diskon baris dari diskon_persen
