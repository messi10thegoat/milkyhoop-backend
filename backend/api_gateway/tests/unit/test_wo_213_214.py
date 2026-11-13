"""BUG-213 (putusan pemilik 13 Nov 22:40, opsi B): Selesai TIDAK menguras sisa WIP (JV-VAR dihapus); tutup bulan yang menguras.
BUG-214: lapor hasil (report-output) saat WO Dirilis DITOLAK berkode WO_BELUM_DIMULAI (ikut Accurate)."""
import inspect
import uuid
from datetime import date
from decimal import Decimal as D
from types import SimpleNamespace

import pytest

from app.routers import production as PR
from app.services.galat_berkode import GalatBerkode

T = "kaos-biru-konveksi"
HARI = date(2026, 11, 13)


class _Tx:
    async def __aenter__(self):
        return None

    async def __aexit__(self, *a):
        return False


class Berhenti(Exception):
    pass


class Conn:
    def __init__(self, status="in_progress"):
        self.status, self.sql = status, []

    def transaction(self):
        return _Tx()

    async def fetchrow(self, sql, *a):
        self.sql.append(" ".join(sql.split()))
        return {"id": uuid.uuid4(), "order_number": "WO-2611-0001", "status": self.status,
                "actual_material_cost": D("100"), "actual_labor_cost": D("0"), "actual_overhead_cost": D("0"),
                "planned_material_cost": D("90"), "planned_labor_cost": D("0"), "planned_overhead_cost": D("0")}

    async def fetchval(self, sql, *a):
        self.sql.append(" ".join(sql.split()))
        return D("40000")                                   # sisa WIP != 0: dulu memicu JV-VAR saat Selesai

    async def execute(self, sql, *a):
        self.sql.append(" ".join(sql.split()))
        if "pg_advisory_xact_lock" in sql and "PRODUCTION_OUTPUT" in str(a):
            raise Berhenti()                                # titik sesudah gerbang status report_output


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

    async def _prasyarat(pool, tid):
        return None
    monkeypatch.setattr(PR, "get_pool", _pool)
    monkeypatch.setattr(PR, "tanggal_dokumen", _tgl)
    monkeypatch.setattr(PR, "_ensure_production_role_preconditions", _prasyarat)


def req():
    return SimpleNamespace(state=SimpleNamespace(user={"tenant_id": T, "user_id": "22222222-2222-2222-2222-222222222222"}))


@pytest.mark.asyncio
async def test_selesai_tidak_menguras_sisa_wip_tanpa_jurnal(monkeypatch):
    conn = Conn("in_progress")
    pasang(monkeypatch, conn)
    h = await PR.complete_order(req(), uuid.uuid4())
    assert h["success"] is True and h["data"]["variance_amount"] == D("10")          # variance_amount (angka) tetap dihitung
    assert any("SET status = 'completed'" in s and "variance_amount" in s for s in conn.sql)
    assert not any("journal_entries" in s or "journal_lines" in s for s in conn.sql)   # NOL jurnal saat Selesai (WIP tidak dibaca, JV-VAR tidak dibuat)
    assert not any("get_next_journal_number" in s or "is_period_closed" in s for s in conn.sql)
    assert conn.sql and all("SUM(jl.debit)" not in s for s in conn.sql)                # sisa WIP tidak dihitung


def test_selesai_sumber_tanpa_pengurasan_dan_penanda_putusan():
    src = inspect.getsource(PR.complete_order)
    assert "PRODUCTION_VARIANCE" not in src and "JV-VAR" not in src and "wip_residual" not in src and "WIP_GENERIC" not in src
    assert "BUG-213" in src and "PRODUCTION_RECONCILE" in src


def test_tutup_bulan_tetap_menguras_wo_selesai():
    s = inspect.getsource(PR.month_end_reconcile)
    assert "PW.sisa_wip_selesai(conn, tenant_id, _wip_id, p_end)" in s and "PW.memo_kuras(" in s                  # satu-satunya penguras sisa WIP WO selesai


@pytest.mark.asyncio
async def test_lapor_hasil_saat_dirilis_ditolak_berkode_tanpa_tulis(monkeypatch):
    conn = Conn("released")
    pasang(monkeypatch, conn)
    with pytest.raises(GalatBerkode) as e:
        await PR.report_output(req(), uuid.uuid4(), SimpleNamespace(good_quantity=D("1"), scrap_quantity=D("0")))
    assert (e.value.status_code, e.value.code) == (400, "WO_BELUM_DIMULAI") and "Mulai proses dulu" in e.value.detail
    assert not any("pg_advisory_xact_lock" in s or "INSERT" in s or "UPDATE" in s for s in conn.sql)   # ditolak SEBELUM kunci/tulis apa pun


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["completed", "cancelled", "draft"])
async def test_lapor_hasil_status_lain_ditolak_berkode(monkeypatch, status):
    conn = Conn(status)
    pasang(monkeypatch, conn)
    with pytest.raises(GalatBerkode) as e:
        await PR.report_output(req(), uuid.uuid4(), SimpleNamespace(good_quantity=D("1"), scrap_quantity=D("0")))
    assert (e.value.status_code, e.value.code) == (400, "WO_TIDAK_BISA_LAPOR") and status in e.value.detail
    assert not any("pg_advisory_xact_lock" in s for s in conn.sql)


@pytest.mark.asyncio
async def test_lapor_hasil_saat_berjalan_lolos_gerbang_status(monkeypatch):
    conn = Conn("in_progress")
    pasang(monkeypatch, conn)
    with pytest.raises(Exception) as e:                                      # Berhenti dibungkus 500 oleh handler; yang dicek: gerbang LOLOS
        await PR.report_output(req(), uuid.uuid4(), SimpleNamespace(good_quantity=D("1"), scrap_quantity=D("0")))
    assert not isinstance(e.value, GalatBerkode) and any("pg_advisory_xact_lock" in s for s in conn.sql)


def test_report_output_sumber_gerbang_status():
    src = inspect.getsource(PR.report_output)
    assert 'order["status"] == "released"' in src and '"WO_BELUM_DIMULAI"' in src and 'order["status"] != "in_progress"' in src
    assert src.index('"WO_BELUM_DIMULAI"') < src.index("pg_advisory_xact_lock")
