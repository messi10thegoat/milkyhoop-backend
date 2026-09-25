"""Backlog 3b (25 Sep 2026): JV-VAR produksi pra-cek periode.

Dulu complete_order membuat jurnal PRODUCTION_VARIANCE tanpa pra-cek: periode CLOSED/LOCKED baru ditolak
trigger prevent_closed_period_journal -> galat DB mentah -> 500. Kini pra-cek memakai fungsi yang SAMA
dengan trigger (is_period_closed) sebelum INSERT jurnal -> 400 bersih, transaksi di-rollback.
"""
import uuid
from datetime import date
from decimal import Decimal as D
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.routers import production as PR

T = "kaos-biru-konveksi"
HARI = date(2026, 9, 25)


class Berhenti(Exception):
    pass


class _Tx:
    async def __aenter__(self):
        return None

    async def __aexit__(self, *a):
        return False


class Conn:
    def __init__(self, tertutup):
        self.tertutup, self.sql = tertutup, []

    def transaction(self):
        return _Tx()

    async def fetchrow(self, sql, *a):
        self.sql.append(sql)
        return {"id": uuid.uuid4(), "order_number": "WO-2609-0001", "status": "in_progress",
                "actual_material_cost": D("100"), "actual_labor_cost": D("0"), "actual_overhead_cost": D("0"),
                "planned_material_cost": D("90"), "planned_labor_cost": D("0"), "planned_overhead_cost": D("0")}

    async def fetchval(self, sql, *a):
        self.sql.append(sql)
        if "is_period_closed" in sql:
            # fake tak bisa mengevaluasi SQL -> kunci BENTUKNYA: persis fungsi yang dipakai trigger
            # prevent_closed_period_journal (is_period_closed), tanpa ekspresi tambahan
            assert " ".join(sql.split()) == "SELECT is_period_closed($1, $2)", sql
            assert a == (T, HARI)
            return self.tertutup
        if "get_next_journal_number" in sql:
            return "JV-VAR-2609-0001"
        return D("1000")                                   # sisa WIP != 0 -> JV-VAR dibuat

    async def execute(self, sql, *a):
        self.sql.append(sql)
        if "INSERT INTO journal_entries" in sql:
            raise Berhenti()                                # cukup sampai titik INSERT jurnal


def pasang(monkeypatch, conn):
    class _Acq:
        async def __aenter__(self):
            return conn

        async def __aexit__(self, *a):
            return False

    async def _pool():
        return SimpleNamespace(acquire=lambda: _Acq())

    async def _tgl(c, t):
        return HARI

    async def _akun(c, t, role):
        return uuid.uuid4()

    monkeypatch.setattr(PR, "get_pool", _pool)
    monkeypatch.setattr(PR, "tanggal_dokumen", _tgl)
    monkeypatch.setattr(PR, "resolve_account_id_by_role", _akun)


def req():
    return SimpleNamespace(state=SimpleNamespace(user={"tenant_id": T, "user_id": "22222222-2222-2222-2222-222222222222"}))


@pytest.mark.asyncio
async def test_periode_tertutup_400_tanpa_jurnal(monkeypatch):
    conn = Conn(tertutup=True)
    pasang(monkeypatch, conn)
    with pytest.raises(HTTPException) as e:
        await PR.complete_order(req(), uuid.uuid4())
    assert e.value.status_code == 400 and "JV-VAR" in str(e.value.detail)
    assert not any("INSERT INTO journal_entries" in s for s in conn.sql)


@pytest.mark.asyncio
async def test_periode_terbuka_lanjut_ke_jurnal(monkeypatch):
    conn = Conn(tertutup=False)
    pasang(monkeypatch, conn)
    with pytest.raises(Exception) as e:
        await PR.complete_order(req(), uuid.uuid4())
    # Berhenti dibungkus 500 oleh handler; yang penting: pra-cek dijalankan DULU, lalu INSERT dicoba
    idx_cek = next(i for i, s in enumerate(conn.sql) if "is_period_closed" in s)
    idx_ins = next(i for i, s in enumerate(conn.sql) if "INSERT INTO journal_entries" in s)
    assert idx_cek < idx_ins
