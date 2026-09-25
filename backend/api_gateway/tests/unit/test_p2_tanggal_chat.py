"""P2 (26 Sep 2026): tanggal yang DITULIS lewat chat = tanggal BISNIS tenant, bukan date.today() UTC.

Situs: tool_executor._apply_relative_dates ("tanggal hari ini/besok/kemarin" -> tanggal faktur/jatuh
tempo), _parse_absolute_date_id (tahun untuk "15 Sep" tanpa tahun), _execute_create_recon_session
(statement_date); workflow_engine auto_create_session_and_import, auto_create_invoice_proposal,
auto_create_payment_proposal. Jam DISUNTIK ke tanggal_tenant: 2026-09-25 18:00Z (= 26 Sep WIB).
"""
import ast
import inspect
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from app.utils import tanggal_tenant as tt

APP = Path(tt.__file__).resolve().parents[1]
UA = APP / "services/unified_agent"
FUNGSI = {
    "tool_executor.py": ["_apply_relative_dates", "_parse_absolute_date_id", "_execute_create_recon_session"],
    "workflow_engine.py": ["auto_create_session_and_import", "auto_create_invoice_proposal",
                           "auto_create_payment_proposal"],
}
MALAM = datetime(2026, 9, 25, 18, 0, tzinfo=timezone.utc)
SIANG = datetime(2026, 9, 25, 10, 0, tzinfo=timezone.utc)


def _alias(tree):
    date_n, dt_n, mod_n = set(), set(), set()
    for n in ast.walk(tree):
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
    return date_n, dt_n, mod_n


def jam_utc(node, alias):
    date_n, dt_n, mod_n = alias

    def nama(b):
        if isinstance(b, ast.Name):
            return b.id
        if isinstance(b, ast.Attribute) and isinstance(b.value, ast.Name) and b.value.id in mod_n:
            return b.value.id + "." + b.attr
        return None
    out = []
    for n in ast.walk(node):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute):
            bn = nama(n.func.value)
            if n.func.attr == "today" and bn and (bn in date_n | dt_n or bn.endswith((".date", ".datetime"))):
                out.append(n.lineno)
    return out


@pytest.mark.parametrize("berkas,nama", [(b, f) for b, fs in FUNGSI.items() for f in fs])
def test_fungsi_tulis_chat_tanpa_tanggal_server(berkas, nama):
    t = ast.parse((UA / berkas).read_text())
    (fn,) = [n for n in ast.walk(t) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == nama]
    assert jam_utc(fn, _alias(t)) == [], (berkas, nama)


def test_penjaga_bisa_merah():
    t = ast.parse("from datetime import date as date_type\nasync def f():\n    return date_type.today()\n")
    assert jam_utc(t.body[1], _alias(t)) == [3]


@pytest.mark.parametrize("berkas", ["tool_executor.py", "orchestrator.py", "workflow_engine.py"])
def test_setiap_panggilan_helper_tanggal_mengoper_hari_ini(berkas):
    t = ast.parse((UA / berkas).read_text())
    panggil = [c for c in ast.walk(t) if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)
               and c.func.id in ("_apply_relative_dates", "_parse_absolute_date_id")]
    for c in panggil:
        kw = [k for k in c.keywords if k.arg == "hari_ini"]
        assert kw, f"{berkas}:{c.lineno} tanpa hari_ini"
        # nilainya WAJIB dari sumber tanggal tenant, bukan konstan/tanggal karangan (date(2026,1,1) pun ditolak)
        v = kw[0].value
        teks = ast.unparse(v)
        sah = (isinstance(v, ast.Name) and v.id == "hari_ini") or "_hari_ini_tenant(" in teks \
            or "self._hari_ini_date()" in teks
        assert sah, f"{berkas}:{c.lineno} hari_ini bukan dari tanggal tenant: {teks}"
    if berkas != "workflow_engine.py":
        assert panggil


def test_helper_wajib_hari_ini_keyword():
    from app.services.unified_agent import tool_executor as TE

    for fn in (TE._apply_relative_dates, TE._parse_absolute_date_id):
        p = inspect.signature(fn).parameters["hari_ini"]
        assert p.kind is inspect.Parameter.KEYWORD_ONLY and p.default is inspect.Parameter.empty
    with pytest.raises(TypeError):
        TE._parse_absolute_date_id("15 februari")


def test_frasa_relatif_dan_tahun_ikut_hari_ini():
    from app.services.unified_agent import tool_executor as TE

    h = date(2026, 9, 26)
    p = TE._apply_relative_dates({}, "buat faktur tanggal hari ini", hari_ini=h)
    assert p.get("invoice_date") == "2026-09-26", p
    p = TE._apply_relative_dates({}, "buat faktur tanggal besok", hari_ini=h)
    assert p.get("invoice_date") == "2026-09-27", p
    # tahun untuk tanggal tanpa tahun = tahun hari_ini (1 Jan 00-07 WIB: UTC masih tahun lalu)
    assert TE._parse_absolute_date_id("tanggal 15 februari", hari_ini=date(2027, 1, 1)) == date(2027, 2, 15)


@pytest.fixture
def jam(monkeypatch):
    def pasang(instant):
        class _DT(datetime):
            @classmethod
            def now(cls, tz=None):
                return instant if tz else instant.replace(tzinfo=None)
        monkeypatch.setattr(tt, "datetime", _DT)
        tt._cache.clear()
    yield pasang
    tt._cache.clear()


class _Conn:
    async def fetchval(self, q, *a):
        assert 'FROM "Tenant"' in q, q
        return "Asia/Jakarta"


class _Pool:
    def acquire(self):
        class _Ctx:
            async def __aenter__(self):
                return _Conn()

            async def __aexit__(self, *e):
                return False
        return _Ctx()


@pytest.mark.parametrize("instant,harap", [(MALAM, "2026-09-26"), (SIANG, "2026-09-25")])
@pytest.mark.asyncio
async def test_workflow_faktur_dan_pembayaran_tanggal_bisnis(jam, monkeypatch, instant, harap):
    from app.services import db_pool
    from app.services.unified_agent import workflow_engine as WF

    jam(instant)

    async def _gp():
        return _Pool()
    monkeypatch.setattr(db_pool, "get_db_pool", _gp)
    ctx = WF.WorkflowContext(tenant_id="kaos-biru-konveksi", data={"customer_id": "c", "items": []})
    assert (await WF._hari_ini_wf(ctx)).isoformat() == harap
    r = await WF.auto_create_invoice_proposal(ctx, None)
    assert r["confirm_suggestion"]["payload"]["invoice_date"] == harap
    # tanggal yang diberikan pengguna tetap menang
    ctx.data["date"] = "2026-08-03"
    r = await WF.auto_create_invoice_proposal(ctx, None)
    assert r["confirm_suggestion"]["payload"]["invoice_date"] == "2026-08-03"
