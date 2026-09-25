"""#10b-3a (f), 26 Sep 2026: GET /api/dashboard/all memanggil fungsi rute LANGSUNG -> default `Query(...)`
tak di-resolve FastAPI; parameter yang tak dioper = objek Query. Bot (query_dashboard_summary /
query_overdue_all) memanggil /all TANPA parameter -> cashFlow/expenses dulu "tgl 1 s/d hari ini"
(`period != "month"` benar untuk objek Query), bukan bulan penuh seperti dashboard FE.
"""
import ast
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import Query

from app.routers import dashboard as D

SRC = Path(D.__file__).read_text()
TENANT = "kaos-biru-konveksi"


def _query_tak_dioper(src: str, pemanggil: str):
    t = ast.parse(src)
    fns = {n.name: n for n in ast.walk(t) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    out = []
    for c in ast.walk(fns[pemanggil]):
        if not (isinstance(c, ast.Call) and isinstance(c.func, ast.Name) and c.func.id in fns):
            continue
        f = fns[c.func.id]
        a = f.args
        dioper = {k.arg for k in c.keywords} | {x.arg for x in a.args[:len(c.args)]}
        params = a.args + a.kwonlyargs
        defaults = [None] * (len(a.args) - len(a.defaults)) + list(a.defaults) + list(a.kw_defaults)
        for p, d in zip(params, defaults):
            if isinstance(d, ast.Call) and getattr(d.func, "id", "") == "Query" and p.arg not in dioper:
                out.append(f"{c.func.id}.{p.arg}")
    return out


def test_all_mengoper_semua_parameter_ber_default_query():
    assert _query_tak_dioper(SRC, "get_dashboard_all") == []


def test_all_memanggil_fungsi_rute_yang_diharapkan():
    t = ast.parse(SRC)
    (f,) = [n for n in ast.walk(t) if isinstance(n, ast.AsyncFunctionDef) and n.name == "get_dashboard_all"]
    dipanggil = {c.func.id for c in ast.walk(f) if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)}
    assert {"get_dashboard_summary", "get_cash_flow_trends", "get_top_expenses"} <= dipanggil


def test_penjaga_bisa_merah():
    contoh = ("from fastapi import Query\n"
              "async def rute(request, period: str = Query('month'), x: int = 1):\n    pass\n"
              "async def get_dashboard_all(request):\n    await rute(request)\n")
    assert _query_tak_dioper(contoh, "get_dashboard_all") == ["rute.period"]


class _Conn:
    def __init__(self):
        self.rentang = []

    async def execute(self, q, *a):
        return "OK"

    async def fetch(self, q, *a):
        self.rentang.append(a[1:3])
        return []

    async def fetchrow(self, q, *a):
        return None


class _Pool:
    def __init__(self, c):
        self.c = c

    def acquire(self):
        pool = self

        class _Ctx:
            async def __aenter__(self):
                return pool.c

            async def __aexit__(self, *e):
                return False
        return _Ctx()


@pytest.mark.parametrize("period,akhir", [
    ("month", date(2026, 9, 30)),                                  # yang kini dioper /all
    (Query("month", regex="^(7d|30d|month)$"), date(2026, 9, 15)),  # objek Query (cacat lama)
])
@pytest.mark.asyncio
async def test_cash_flow_rentang_bulan_penuh_hanya_bila_period_str(monkeypatch, period, akhir):
    c = _Conn()

    async def _pool():
        return _Pool(c)

    async def _hari(conn, t):
        return date(2026, 9, 15)
    monkeypatch.setattr(D, "get_db_pool", _pool)
    monkeypatch.setattr(D, "tanggal_dokumen", _hari)
    req = SimpleNamespace(state=SimpleNamespace(user={"tenant_id": TENANT}))
    await D.get_cash_flow_trends(req, period=period, start_date=None, end_date=None)
    assert c.rentang and c.rentang[0] == (date(2026, 9, 1), akhir), c.rentang
