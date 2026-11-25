"""FIFO unit 2 (26 Nov 2026): sambungan lapisan ke helper kanonik. Flag/barang bukan FIFO -> jalur WAC lama tanpa satu panggilan FIFO pun.
SQL lapisan diuji di Postgres nyata: scripts/uji-fifo-pg.sh (V510 + fungsi fifo_lapisan, T1/T4/T5/kekekalan)."""
import asyncio
import datetime
from decimal import Decimal as D
from uuid import uuid4
import os
os.environ.setdefault("OPENAI_API_KEY", "sk-boneka-unit-test-tanpa-jaringan")

import pytest

from app.services import fifo_lapisan as F
from app.services import inventory_helpers as IH

TGL = datetime.date(2026, 11, 26)


class _K:
    def __init__(self):
        self.exec, self.kartu = [], []

    async def fetchval(self, sql, *a):
        if "stock_opnames" in sql:
            return None
        if "SUM(quantity_in - quantity_out)" in sql:
            return D(10)
        if "journal_entries" in sql:
            return 0
        return D("33.33")

    async def fetchrow(self, sql, *a):
        return None   # resolve_inventory_accounts: tanpa akun -> tanpa jurnal HPP

    async def execute(self, sql, *a):
        self.exec.append((sql, a))


def _pasang(monkeypatch, aktif, ambil=None):
    log = []

    async def fifo_aktif(conn, tid, pid):
        return aktif

    async def ambil_keluar(conn, tid, pid, q):
        log.append(("ambil", q)); return ambil

    async def rata(conn, tid, pid, a):
        return D("9.00")

    async def pakai(conn, tid, lid, a):
        log.append(("pakai", lid, a))

    async def kartu(conn, **kw):
        log.append(("kartu", kw["id"], kw["total_cost"], kw["unit_cost"]))

    async def biaya(conn, tid, pid, qty=None):
        log.append(("wac",)); return {"unit_cost": D("33.33"), "cost_source": "WEIGHTED_AVG"}

    for n, f in (("fifo_aktif", fifo_aktif), ("ambil_keluar", ambil_keluar), ("rata_sisa_sesudah", rata), ("catat_pakai", pakai)):
        monkeypatch.setattr(F, n, f)
    monkeypatch.setattr(IH, "tulis_kartu", kartu)
    monkeypatch.setattr(IH, "get_cost_for_sale", biaya)
    return log


def _keluar(k):
    return asyncio.run(IH.record_inventory_outbound(k, "t", uuid4(), "X", "X", uuid4(), 3, "SALES_INVOICE", uuid4(), "INV-1", None, "uji", receipt_date=TGL))


def test_keluar_fifo_hpp_sigma_lapisan_dan_moves_sesudah_kartu(monkeypatch):
    ambil = [F.Ambil("a", D(2), D("66.67")), F.Ambil("b", D(1), D("50.00"))]
    log = _pasang(monkeypatch, True, ambil)
    r = _keluar(_K())
    assert r["total_cost"] == D("116.67") and r["cost_source"] == "FIFO" and r["unit_cost"] == D("38.89")
    assert [x[0] for x in log] == ["ambil", "kartu", "pakai"]                       # kunci+rencana -> kartu -> moves (FK)
    assert log[1][2] == D("116.67") and log[2][1] == log[1][1] == r["ledger_id"] and log[2][2] == ambil


def test_keluar_wac_tak_menyentuh_fifo(monkeypatch):
    log = _pasang(monkeypatch, False)
    r = _keluar(_K())
    assert r["total_cost"] == D("99.99") and [x[0] for x in log] == ["wac", "kartu"]


def test_stok_fifo_kurang_jadi_409(monkeypatch):
    async def fifo_aktif(conn, tid, pid): return True
    async def ambil_keluar(conn, tid, pid, q): raise F.StokFifoKurang(D(3), D(2))
    monkeypatch.setattr(F, "fifo_aktif", fifo_aktif); monkeypatch.setattr(F, "ambil_keluar", ambil_keluar)
    with pytest.raises(Exception) as e:
        _keluar(_K())
    assert getattr(e.value, "code", None) == "STOK_FIFO_KURANG" and e.value.status_code == 409


def test_fifo_aktif_hanya_true_asli():
    class C:
        def __init__(self, v): self.v = v
        async def fetchval(self, sql, *a):
            assert "f.feature = $2" in sql and "costing_mulai IS NOT NULL" in sql and a[1] == "fifo_v1"; return self.v
    assert asyncio.run(F.fifo_aktif(C(True), "t", "p")) is True
    assert asyncio.run(F.fifo_aktif(C(D(1)), "t", "p")) is False and asyncio.run(F.fifo_aktif(C(None), "t", "p")) is False


def test_masuk_dan_pembalik_bersambung():
    import inspect
    s = inspect.getsource(IH.record_inventory_inbound)
    assert s.index("tulis_kartu(") < s.index("fifo_lapisan.catat_masuk(") and "fifo_lapisan.nilai_masuk(quantity_dec, unit_cost_dec)" in s
    v = inspect.getsource(IH.record_inventory_reversal)
    assert v.index("fifo_lapisan.rencana_balik(") < v.index("tulis_kartu(") < v.index("fifo_lapisan.catat_balik(")
    assert "total_cost = rencana_fifo.nilai" in v
    assert F.nilai_masuk(3, "33.335") == D("100.01")
