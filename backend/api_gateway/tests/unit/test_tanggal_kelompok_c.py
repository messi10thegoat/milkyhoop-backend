"""Tanggal-server kelompok C (26 Sep 2026): "hari ini" = tanggal BISNIS tenant, bukan UTC server.

deliveries.today_count, document_intake.posted_today (timestamptz -> tanggal_bisnis($1, updated_at)),
sales_receipts daily-summary default summary_date, currencies latest-rates as_of + convert rate_date default.
Pada 00:00-07:00 WIB tanggal UTC masih KEMARIN -> kartu "hari ini" menghitung hari yang salah.
Jam DISUNTIK ke tanggal_tenant: 2026-09-25 18:00Z (= 26 Sep WIB).
"""
import ast
import re
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.utils import tanggal_tenant as tt

APP = Path(tt.__file__).resolve().parents[1]
BERKAS = ["routers/deliveries.py", "routers/document_intake.py", "routers/sales_receipts.py", "routers/currencies.py"]
MALAM = datetime(2026, 9, 25, 18, 0, tzinfo=timezone.utc)
TENANT = "kaos-biru-konveksi"


def situs_utc(src: str):
    t = ast.parse(src)
    date_n, dt_n, mod_n = set(), set(), set()
    for n in ast.walk(t):
        if isinstance(n, ast.ImportFrom) and n.module == "datetime":
            for a in n.names:
                if a.name == "date":
                    date_n.add(a.asname or "date")
                if a.name == "datetime":
                    dt_n.add(a.asname or "datetime")
        if isinstance(n, ast.Import):
            for a in n.names:
                if a.name == "datetime":
                    mod_n.add(a.asname or "datetime")

    def nama(b):
        if isinstance(b, ast.Name):
            return b.id
        if isinstance(b, ast.Attribute) and isinstance(b.value, ast.Name) and b.value.id in mod_n:
            return b.value.id + "." + b.attr
        return None
    out = []
    for n in ast.walk(t):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "today":
            bn = nama(n.func.value)
            if bn and (bn in date_n | dt_n or bn.endswith((".date", ".datetime"))):
                out.append(n.lineno)
        if isinstance(n, ast.Constant) and isinstance(n.value, str) and "SELECT" in n.value.upper() \
                and re.search(r"\bCURRENT_DATE\b|::date\s*=\s*CURRENT_DATE", n.value):
            out.append(n.lineno)
    return sorted(out)


@pytest.mark.parametrize("berkas", BERKAS)
def test_tanpa_tanggal_server(berkas):
    assert situs_utc((APP / berkas).read_text()) == [], berkas


def test_penjaga_bisa_merah():
    assert situs_utc("from datetime import date as d\ndef f():\n    return d.today()\n"
                     "Q = 'SELECT COUNT(*) FILTER (WHERE x = CURRENT_DATE) FROM t'\n") == [3, 4]


def test_sql_hari_ini_tanggal_bisnis_tenant():
    dl = (APP / "routers/deliveries.py").read_text()
    di = (APP / "routers/document_intake.py").read_text()
    assert "fulfillment_date = tanggal_bisnis($1)" in dl
    # timestamptz dikonversi SEKALI ke zona tenant (bukan updated_at::date = UTC)
    assert "tanggal_bisnis($1, updated_at) = tanggal_bisnis($1)" in di
    assert "updated_at::date" not in di


@pytest.fixture
def jam(monkeypatch):
    class _DT(datetime):
        @classmethod
        def now(cls, tz=None):
            return MALAM if tz else MALAM.replace(tzinfo=None)
    monkeypatch.setattr(tt, "datetime", _DT)
    tt._cache.clear()
    yield
    tt._cache.clear()


class _Conn:
    def __init__(self):
        self.args = []

    async def execute(self, q, *a):
        return "OK"

    async def fetchval(self, q, *a):
        assert 'FROM "Tenant"' in q, q
        return "Asia/Jakarta"

    async def fetchrow(self, q, *a):
        self.args.append((q, a))
        if "is_base_currency" in q:
            return {"code": "IDR"}
        return None

    async def fetch(self, q, *a):
        self.args.append((q, a))
        return []


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


def _pasang(monkeypatch, mod, c):
    async def _gp():
        return _Pool(c)
    monkeypatch.setattr(mod, "get_pool", _gp)
    monkeypatch.setattr(mod, "get_user_context", lambda r: {"tenant_id": TENANT})


@pytest.mark.asyncio
async def test_ringkasan_harian_default_tanggal_bisnis(jam, monkeypatch):
    from app.routers import sales_receipts as SR

    c = _Conn()
    _pasang(monkeypatch, SR, c)
    try:
        await SR.get_daily_summary(request=None, summary_date=None, warehouse_id=None)
    except Exception:
        pass   # baris kosong -> respons bisa gagal dibentuk; yang diuji argumen kueri
    (q, a) = [x for x in c.args if "get_daily_sales_summary" in x[0]][0]
    assert a[1] == date(2026, 9, 26), a
    c2 = _Conn()
    _pasang(monkeypatch, SR, c2)
    try:
        await SR.get_daily_summary(request=None, summary_date=date(2026, 8, 3), warehouse_id=None)
    except Exception:
        pass
    (q, a) = [x for x in c2.args if "get_daily_sales_summary" in x[0]][0]
    assert a[1] == date(2026, 8, 3)                      # tanggal eksplisit tetap menang


@pytest.mark.asyncio
async def test_kurs_terbaru_as_of_tanggal_bisnis(jam, monkeypatch):
    from app.routers import currencies as CU

    c = _Conn()
    _pasang(monkeypatch, CU, c)
    r = await CU.get_latest_rates(request=None)
    assert r.as_of == "2026-09-26", r.as_of
