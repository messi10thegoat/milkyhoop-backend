"""BUG-450: Kirim dari Faktur memakai lapisan FIFO untuk barang FIFO aktif (HPP 5x10.000 + 1x12.000 = 62.000), bukan WAC; selain itu WAC lama."""
import asyncio
import inspect
from decimal import Decimal as D

import pytest
from fastapi import HTTPException

import app.routers.sales_invoices as SI
from app.services import inventory_helpers as IH
from app.services.fifo_lapisan import Ambil


class Kon:
    def __init__(self, wac=11000):
        self.wac = wac

    async def fetchval(self, sql, *a):
        assert "get_weighted_average_cost" in sql
        return self.wac


def _jalan(kon, ambil, qd=D(6)):
    async def fake(conn, tid, pid, q):
        return ambil
    asli = IH._fifo_ambil
    IH._fifo_ambil = fake
    try:
        return asyncio.run(SI._biaya_kirim(kon, "t", "p", qd, "Kaos"))
    finally:
        IH._fifo_ambil = asli


def test_fifo_aktif_hpp_dari_lapisan_bukan_wac():
    ambil = [Ambil("L1", D(5), D(50000)), Ambil("L2", D(1), D(12000))]
    r = _jalan(Kon(), ambil)
    assert r["line_cogs"] == D("62000.00") and r["avg_cost"] == D("10333.33") and r["ambil"] is ambil


def test_bukan_fifo_tetap_wac():
    r = _jalan(Kon(wac=11000), None)
    assert r["line_cogs"] == D("66000.00") and r["avg_cost"] == D(11000) and r["ambil"] is None


def test_bukan_fifo_wac_nol_ditolak():
    with pytest.raises(HTTPException) as e:
        _jalan(Kon(wac=0), None)
    assert e.value.status_code == 409 and "WAC = 0" in e.value.detail


def test_inti_mencatat_pakai_dan_rata_sisa():
    src = inspect.getsource(SI._execute_fulfillment)
    assert "_biaya_kirim(" in src and "catat_pakai(conn, tenant_id, _lid, _ambil)" in src and "rata_sisa_sesudah" in src and "get_weighted_average_cost" not in src
