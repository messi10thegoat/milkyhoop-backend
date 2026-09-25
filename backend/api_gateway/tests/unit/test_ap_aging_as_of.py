"""AP aging menolak as_of lampau seperti AR (tiket #10b-3a (b), 25 Sep 2026).

get_ap_aging_summary(tenant, as_of) memakai compute_ap_outstanding (sisa HARI INI) dan hanya
menyaring bill_date <= as_of -> as_of lampau = utang hari ini berlabel tanggal lampau. Dipanggil bot
(direct_action_registry QueryParam as_of "Per Tanggal"); FE & nginx 14 hari: 0.
"""
from datetime import date
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.routers import reports as R

TENANT = "grapgrap-manado"
HARI_INI = date(2026, 9, 25)


class DB:
    def __init__(self):
        self.sql = []

    async def execute(self, sql, *a):
        self.sql.append(sql)

    async def fetchrow(self, sql, *a):
        self.sql.append(sql)
        assert "get_ap_aging_summary" in sql and a == (TENANT, self.harap)
        return {k: 0 for k in ("total_current", "total_1_30", "total_31_60", "total_61_90", "total_91_120",
                               "total_over_120", "grand_total", "overdue_count")}


class Pool:
    def __init__(self, db):
        self.db = db

    def acquire(self):
        db = self.db

        class A:
            async def __aenter__(self):
                return db

            async def __aexit__(self, *e):
                return False
        return A()


@pytest.fixture
def db(monkeypatch):
    d = DB()

    async def _pool():
        return Pool(d)

    async def _hari(conn, tid):
        assert tid == TENANT
        return HARI_INI
    monkeypatch.setattr(R, "get_pool", _pool)
    monkeypatch.setattr(R, "tanggal_dokumen", _hari)
    return d


def _req():
    return SimpleNamespace(state=SimpleNamespace(user={"tenant_id": TENANT, "user_id": None}), headers={})


@pytest.mark.asyncio
async def test_as_of_lampau_ditolak_422_tanpa_menjalankan_fungsi(db):
    with pytest.raises(HTTPException) as e:
        await R.get_ap_aging_summary(_req(), as_of=date(2026, 8, 31))
    assert e.value.status_code == 422 and e.value.detail.startswith("Umur utang per tanggal lampau")
    assert not any("get_ap_aging_summary" in s for s in db.sql)


@pytest.mark.asyncio
@pytest.mark.parametrize("as_of,harap", [(None, HARI_INI), (HARI_INI, HARI_INI), (date(2026, 10, 31), date(2026, 10, 31))])
async def test_hari_ini_dan_mendatang_tetap_jalan(db, as_of, harap):
    db.harap = harap
    out = await R.get_ap_aging_summary(_req(), as_of=as_of)
    assert out.as_of_date == harap


def test_pesan_ar_tak_berubah():
    with pytest.raises(HTTPException) as e:
        R._tolak_as_of_lampau(date(2026, 1, 1), HARI_INI)
    assert e.value.detail == "Umur piutang per tanggal lampau belum tersedia; saldo dihitung dari jurnal per hari ini."
