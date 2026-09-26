"""Audit pay-group PG1–PG3 (26 Sep 2026): aksi run gaji, pembayaran gaji, buat karyawan.

Aturan (memori 20 Sep): SETIAP endpoint ber-employee_id, baca ATAU tulis, wajib
menyaring pay-group. GET run & /slips sudah; aksi run (calculate/submit/approve/
reject/post/void) + payroll-payments + POST /employees hanya cek tenant.
Run/slip TAK menyimpan snapshot grup; run TAK punya pay_group_id.
"""
import inspect
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.testclient import TestClient

from app.services import pay_group_access as PGA
from app.routers import payroll_runs as PR
from app.routers import payroll_payments as PP
from app.routers import employees as EM

GA, GB = "11111111-0000-0000-0000-00000000000a", "11111111-0000-0000-0000-00000000000b"
E1, E2, E3 = ("22222222-0000-0000-0000-00000000000" + c for c in "123")
RUN = "33333333-0000-0000-0000-000000000001"
PAY = "44444444-0000-0000-0000-000000000001"


class _Conn:
    def __init__(self, run_emp, grup, pay_run=RUN):
        self.run_emp, self.grup, self.pay_run = run_emp, grup, pay_run
        self.q = []

    async def fetch(self, sql, *a):
        self.q.append(sql)
        if "payroll_run_employees" in sql:
            return [{"employee_id": e} for e in self.run_emp]
        if "FROM employees" in sql:
            return [{"id": e, "pay_group_id": self.grup[e]} for e in a[1] if e in self.grup]
        raise AssertionError("kueri sesudah penjaga: " + sql[:60])

    async def fetchval(self, sql, *a):
        self.q.append(sql)
        if "FROM payroll_payments" in sql:
            return self.pay_run
        raise AssertionError("kueri sesudah penjaga: " + sql[:60])

    async def fetchrow(self, sql, *a):
        self.q.append(sql)
        raise AssertionError("kueri sesudah penjaga: " + sql[:60])

    async def execute(self, sql, *a):
        if sql.strip().startswith("SET"):
            return
        self.q.append(sql)
        raise AssertionError("tulis sesudah penjaga: " + sql[:60])


@pytest.fixture
def peran(monkeypatch):
    st = {"role": "STAFF", "akses": [GA]}

    async def role(uid, tid, conn):
        return st["role"]

    async def akses(uid, tid, role, conn):
        return list(st["akses"])

    for m in (PGA, PR, EM):
        monkeypatch.setattr(m, "get_user_role_code", role, raising=False)
        monkeypatch.setattr(m, "get_accessible_pay_group_ids", akses, raising=False)
    return st


async def _cek(conn, uid="u1"):
    return await PGA.run_dalam_cakupan(conn, "t", uid, RUN)


# ---------- helper ----------

@pytest.mark.asyncio
async def test_run_seluruhnya_dalam_cakupan(peran):
    assert await _cek(_Conn([E1, E2], {E1: GA, E2: GA})) is True


@pytest.mark.asyncio
async def test_run_campuran_ditolak(peran):
    assert await _cek(_Conn([E1, E2], {E1: GA, E2: GB})) is False


@pytest.mark.asyncio
async def test_run_kosong_bukan_pintas(peran):
    """Kasus tepi 1: 0 karyawan/slip -> TIDAK (run tak punya pay_group_id; kosong ≠ boleh)."""
    assert await _cek(_Conn([], {})) is False


@pytest.mark.asyncio
async def test_run_kosong_owner_boleh(peran):
    peran["role"] = "OWNER"
    assert await _cek(_Conn([], {})) is True


@pytest.mark.asyncio
async def test_karyawan_pindah_grup_dinilai_grup_saat_ini(peran):
    """Kasus tepi 2: run/slip TAK menyimpan snapshot grup -> grup SAAT INI dipakai,
    konsisten dengan GET /payroll/{id} dan /slips yang sudah tayang. E2 dulu GA,
    kini GB -> run tidak lagi dalam cakupan penuh."""
    assert await _cek(_Conn([E1, E2], {E1: GA, E2: GB})) is False
    assert await _cek(_Conn([E1, E2], {E1: GA, E2: GA})) is True


@pytest.mark.asyncio
@pytest.mark.parametrize("grup", [{E1: GA}, {E1: GA, E2: None}])
async def test_baris_hilang_atau_tanpa_grup_tertutup(peran, grup):
    assert await _cek(_Conn([E1, E2], grup)) is False


@pytest.mark.asyncio
async def test_tanpa_akses_atau_tanpa_user(peran):
    peran["akses"] = []
    assert await _cek(_Conn([E1], {E1: GA})) is False
    peran["akses"] = [GA]
    assert await _cek(_Conn([E1], {E1: GA}), uid=None) is False


# ---------- rute: 404 sebelum kueri run apa pun (tanpa bocor total) ----------

class _Acq:
    def __init__(self, c):
        self.c = c

    async def __aenter__(self):
        return self.c

    async def __aexit__(self, *a):
        return False


class _Pool:
    def __init__(self, c):
        self.c = c

    def acquire(self):
        return _Acq(self.c)


class _SetUser(BaseHTTPMiddleware):
    async def dispatch(self, req, nxt):
        req.state.user = {"user_id": "55555555-0000-0000-0000-000000000001", "tenant_id": "t", "role": "ADMIN"}
        return await nxt(req)


def _klien(monkeypatch, conn):
    async def pool():
        return _Pool(conn)

    for m in (PR, PP, EM):
        monkeypatch.setattr(m, "get_pool", pool)
    app = FastAPI()
    app.include_router(PR.router, prefix="/api/payroll")
    app.include_router(PP.router, prefix="/api/payroll-payments")
    app.include_router(EM.router, prefix="/api/employees")
    app.add_middleware(_SetUser)
    return TestClient(app, raise_server_exceptions=False)


@pytest.mark.parametrize("aksi,badan", [
    ("calculate", None), ("submit", None), ("approve", None),
    ("reject", {"reason": "x"}), ("post", None), ("void", {"reason": "x"}),
])
def test_aksi_run_campuran_404_tanpa_kueri_lain(monkeypatch, peran, aksi, badan):
    c = _Conn([E1, E2], {E1: GA, E2: GB})
    r = _klien(monkeypatch, c).post(f"/api/payroll/{RUN}/{aksi}", json=badan)
    assert r.status_code == 404, (aksi, r.status_code, r.text)
    assert "total" not in r.text.lower()
    assert not any("payroll_runs" in s for s in c.q)


def test_pembayaran_daftar_dan_buat_run_campuran_404(monkeypatch, peran):
    c = _Conn([E1, E2], {E1: GA, E2: GB})
    k = _klien(monkeypatch, c)
    assert k.get(f"/api/payroll-payments/by-payroll/{RUN}").status_code == 404
    r = k.post("/api/payroll-payments", json={"payroll_id": RUN, "payment_type": "salary",
                                             "payment_date": "2026-09-26", "bank_account_id": None})
    assert r.status_code == 404, r.text
    assert not any("payroll_runs" in s for s in c.q)


@pytest.mark.parametrize("aksi,badan", [("post", None), ("void", {"reason": "x"})])
def test_pembayaran_post_void_run_campuran_404(monkeypatch, peran, aksi, badan):
    c = _Conn([E1, E2], {E1: GA, E2: GB})
    r = _klien(monkeypatch, c).post(f"/api/payroll-payments/{PAY}/{aksi}", json=badan)
    assert r.status_code == 404, r.text


def test_kontrol_positif_run_dalam_cakupan_melewati_penjaga(monkeypatch, peran):
    c = _Conn([E1], {E1: GA})
    _klien(monkeypatch, c).post(f"/api/payroll/{RUN}/submit")
    # penjaga lolos -> handler lanjut membaca payroll_runs (tiruan menolak = bukti sampai sana)
    assert any("payroll_runs" in s for s in c.q)


# ---------- PG3 ----------

def test_buat_karyawan_di_grup_luar_cakupan_404(monkeypatch, peran):
    c = _Conn([], {})
    r = _klien(monkeypatch, c).post("/api/employees", json={"name": "X", "pay_group_id": GB})
    assert r.status_code == 404, r.text
    assert not any("INSERT" in s for s in c.q)


def test_penjaga_pg3_ada_sebelum_insert():
    src = inspect.getsource(EM.create_employee)
    assert src.index("get_accessible_pay_group_ids") < src.index("INSERT INTO employees")


def test_enam_aksi_dijaga_sebelum_baca_run():
    for fn in ("calculate_payroll", "submit_payroll", "approve_payroll", "reject_payroll", "post_payroll", "void_payroll"):
        src = inspect.getsource(getattr(PR, fn))
        assert "run_dalam_cakupan" in src, fn
        assert src.index("run_dalam_cakupan") < src.index("FROM payroll_runs"), fn
