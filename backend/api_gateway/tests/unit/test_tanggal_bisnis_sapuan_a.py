"""Sapuan tanggal bisnis A (26 Sep 2026): layar uang/operasi tanpa CURRENT_DATE / date.today() UTC.

Pemindai sadar-alias (import-from alias, `import datetime as x`, X.today(), X.now()/utcnow().date(), SQL
CURRENT_DATE) menemukan 118 titik yang lolos #10; unit A = layar yang dipakai grapgrap. Probe perilaku DUA SISI
(DB scratch salinan penuh, sesi UTC seperti prod; UTC 25 Sep vs bisnis 26 Sep): kode baru — config gaji
berakhir kemarin tak tampil, tagihan jatuh tempo kemarin overdue 1 hari, pembayaran hari ini masuk today_amount
100.000; kode lama — ketiganya salah.
"""
import ast
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

APP = Path(__file__).resolve().parents[2] / "app"
SASARAN = {
    "routers/bill_payments.py": ["get_bill_payments_summary", "get_vendor_open_bills"],
    "routers/payroll.py": ["get_payroll_summary"],
    "routers/employees.py": ["get_salary_config"],
    "routers/periods.py": ["get_current_period"],
    "routers/kasbank.py": ["get_account_summary"],
    "routers/expense_extended.py": ["get_approval_stats", "get_insight"],
    "routers/financial_ratios.py": ["get_current_ratios", "get_ratio_dashboard", "compare_to_benchmark"],
    "routers/reports_profitability.py": ["_parse_dates"],
}
KASUS = [(b, n) for b, ns in SASARAN.items() for n in ns]
SQL_UTC = ("CURRENT_DATE", "date.today()", "now()::date", "NOW()::DATE")


def _fungsi(berkas, nama):
    src = (APP / berkas).read_text()
    for n in ast.walk(ast.parse(src)):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == nama:
            return n, ast.get_source_segment(src, n)
    raise AssertionError(f"{berkas}::{nama} tidak ditemukan")


@pytest.mark.parametrize("berkas,nama", KASUS)
def test_tanpa_tanggal_server(berkas, nama):
    _, seg = _fungsi(berkas, nama)
    kode = "\n".join(l.split("#")[0] for l in seg.splitlines())          # abaikan komentar
    for pola in SQL_UTC:
        assert pola not in kode, (nama, pola)


@pytest.mark.parametrize("berkas,nama", [k for k in KASUS if k[1] != "_parse_dates"])
def test_memakai_tanggal_bisnis(berkas, nama):
    n, _ = _fungsi(berkas, nama)
    assert any(isinstance(c, ast.Call) and getattr(c.func, "id", None) == "tanggal_dokumen" for c in ast.walk(n)), nama


def test_profitabilitas_pemanggil_menyuntik_tanggal_bisnis():
    src = (APP / "routers/reports_profitability.py").read_text()
    assert src.count("_parse_dates(start_date, end_date, await tanggal_dokumen(conn, tenant_id))") == 3


def test_ringkasan_pembayaran_keluar_satu_definisi_dengan_penerimaan():
    n, seg = _fungsi("routers/bill_payments.py", "get_bill_payments_summary")
    assert "batas_periode(await tanggal_dokumen(conn, ctx[\"tenant_id\"]))" in seg
    assert "*argumen_kueri(ctx[\"tenant_id\"], _b)" in seg
    s = " ".join(seg.split())
    assert "payment_date BETWEEN $3::date AND $4::date" in s and "payment_date BETWEEN $5::date AND $6::date" in s


# ---------- pay-group: salary-config tetap disaring SEBELUM kueri tanggal ----------

class Conn:
    def __init__(self):
        self.calls = []

    async def execute(self, sql, *a):
        self.calls.append(("execute", sql, a))

    async def fetch(self, sql, *a):
        self.calls.append(("fetch", sql, a))
        return []


def _pasang(monkeypatch, conn, dalam_lingkup):
    from app.routers import employees as E
    from app.services import pay_group_access as PGA

    class _Acq:
        async def __aenter__(self):
            return conn

        async def __aexit__(self, *a):
            return False

    async def _pool():
        return SimpleNamespace(acquire=lambda: _Acq())

    async def _lingkup(c, t, u, e):
        return dalam_lingkup

    async def _tgl(c, t):
        from datetime import date
        return date(2026, 9, 26)

    monkeypatch.setattr(E, "get_pool", _pool)
    monkeypatch.setattr(PGA, "employee_in_scope", _lingkup)
    monkeypatch.setattr(E, "tanggal_dokumen", _tgl)
    return E


def _req():
    return SimpleNamespace(state=SimpleNamespace(user={"tenant_id": "kaos-biru-konveksi", "user_id": str(uuid.uuid4())}))


@pytest.mark.asyncio
async def test_salary_config_di_luar_pay_group_404_tanpa_kueri(monkeypatch):
    conn = Conn()
    E = _pasang(monkeypatch, conn, dalam_lingkup=False)
    with pytest.raises(HTTPException) as e:
        await E.get_salary_config(_req(), uuid.uuid4())
    assert e.value.status_code == 404
    assert not [c for c in conn.calls if c[0] == "fetch"]


@pytest.mark.asyncio
async def test_salary_config_dalam_pay_group_pakai_tanggal_bisnis(monkeypatch):
    from datetime import date
    conn = Conn()
    E = _pasang(monkeypatch, conn, dalam_lingkup=True)
    await E.get_salary_config(_req(), uuid.uuid4())
    f = [c for c in conn.calls if c[0] == "fetch"]
    assert len(f) == 1 and "esc.end_date >= $3::date" in f[0][1] and f[0][2][2] == date(2026, 9, 26)
