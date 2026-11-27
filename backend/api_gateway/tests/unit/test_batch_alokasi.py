"""Paritas butir 9 (V515): alokasi beberapa batch per baris Faktur Penjualan; kirim dipecah per batch."""
import asyncio
import uuid
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi import HTTPException

from app.services import batch_alokasi as BA

A, B, P, L = uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4()


class K:
    def __init__(self, ada=True, alok=(), kirim=(), milik=(A, B), tb=True, dasar="Pcs"):
        self.ada, self.alok, self.kirim, self.milik, self.tb, self.dasar, self.tulis = ada, list(alok), list(kirim), set(milik), tb, dasar, []

    async def fetchval(self, sql, *a):
        if "to_regclass" in sql:
            return self.ada
        if "FROM sales_invoice_items" in sql:
            return L
        raise AssertionError(sql)

    async def fetchrow(self, sql, *a):
        return {"nama_produk": "Susu", "tb": self.tb, "dasar": self.dasar}

    async def fetch(self, sql, *a):
        if "FROM item_batches" in sql:
            return [{"id": i} for i in a[2] if i in self.milik]
        if "invoice_fulfillment_items" in sql:
            return self.kirim
        return self.alok

    async def execute(self, sql, *a):
        self.tulis.append(a)


def test_pecah_kirim_per_batch_dikurangi_terkirim():
    k = K(alok=[{"invoice_item_id": L, "batch_id": A, "quantity": Decimal(6)}, {"invoice_item_id": L, "batch_id": B, "quantity": Decimal(4)}],
          kirim=[{"invoice_item_id": L, "batch_id": A, "q": Decimal(5)}])
    r = asyncio.run(BA.pecah_kirim(k, [{"invoice_item_id": L, "quantity": Decimal(3)}]))
    assert [(x["batch_id"], x["quantity"]) for x in r] == [(A, Decimal(1)), (B, Decimal(2))]


def test_tanpa_alokasi_tak_berubah_dan_batch_pilihan_menang():
    k = K()
    req = [{"invoice_item_id": L, "quantity": Decimal(3), "batch_id": A}]
    assert asyncio.run(BA.pecah_kirim(k, req)) == req
    assert asyncio.run(BA.pecah_kirim(K(ada=False), [{"invoice_item_id": L, "quantity": 1}])) == [{"invoice_item_id": L, "quantity": 1}]


def _kode(k, al, qty=10, unit=None):
    with pytest.raises(HTTPException) as e:
        asyncio.run(BA.simpan(k, "t", "inv", 1, P, qty, unit, al))
    return e.value.detail["code"]


def test_simpan_validasi():
    ok = [{"batch_id": A, "quantity": 6}, {"batch_id": B, "quantity": 4}]
    k = K()
    asyncio.run(BA.simpan(k, "t", "inv", 1, P, 10, "pcs", ok))
    assert [t[2] for t in k.tulis] == [A, B]
    assert asyncio.run(BA.simpan(K(), "t", "inv", 1, P, 10, None, None)) is None
    assert _kode(K(), [{"batch_id": A, "quantity": 6}]) == "ALOKASI_JUMLAH_TAK_SAMA"
    assert _kode(K(), [{"batch_id": A, "quantity": 5}, {"batch_id": A, "quantity": 5}]) == "ALOKASI_BATCH_GANDA"
    assert _kode(K(milik=(A,)), ok) == "ALOKASI_BATCH_BUKAN_BARANG"
    assert _kode(K(tb=False), ok) == "ALOKASI_BARANG_TANPA_BATCH"
    assert _kode(K(), ok, unit="Dus") == "ALOKASI_SATUAN_BUKAN_DASAR"
    assert _kode(K(ada=False), ok) == "ALOKASI_BATCH_BELUM_AKTIF"


def test_migrasi_v515_aditif():
    m = Path(__file__).resolve().parents[3] / "migrations/V515__alokasi_batch_baris_faktur.sql"
    t = m.read_text()
    assert "CREATE TABLE sales_invoice_item_batches" in t and "DROP" not in t and "ALTER TABLE sales_invoice_items" not in t
