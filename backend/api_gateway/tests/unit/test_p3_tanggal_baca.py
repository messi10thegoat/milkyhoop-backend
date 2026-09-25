"""P3 (26 Sep 2026): tanggal BACA di jalur chat/agen = tanggal BISNIS tenant, bukan jam UTC server.

"bulan ini"/periode (period_resolver, driver_deltas, projection_engine, clarification_slots, orchestrator),
default tanggal kueri (tool_executor._execute_query), subjudul grafik, daftar jatuh tempo bot (SQL
CURRENT_DATE -> tanggal_bisnis($1)), restock 90 hari, deteksi duplikat 7 hari. Pada 00:00-07:00 WIB
tgl 1 jam UTC masih di bulan LALU -> jawaban bot untuk bulan yang salah.
"""
import ast
import inspect
import re
from datetime import date
from pathlib import Path

import pytest

from app.utils import tanggal_tenant as tt

APP = Path(tt.__file__).resolve().parents[1]
UA = APP / "services/unified_agent"
BERKAS = sorted(UA.rglob("*.py")) + [APP / "services/financial_intelligence.py"]
# Situs tanggal-server yang SENGAJA tersisa (cadangan berjaga, semuanya dipanggil dengan tanggal tenant):
DIIZINKAN = {
    ("system_prompt.py", "build_system_messages"),  # `today or ...`; satu-satunya pemanggil hidup (orchestrator) mengoper _hari_ini_tenant
    ("system_prompt.py", "build_system_prompt"),    # DEPRECATED; hanya dipakai routers/streaming_chat (tak dipasang)
    ("tool_executor.py", "_dasar_jatuh_tempo"),     # cadangan ber-log [K0_ZONA] bila hari_ini hilang
}


def _situs_utc(path: Path):
    src = path.read_text()
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

    fns = [n for n in ast.walk(t) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]

    def terdalam(ln):
        calon = [f for f in fns if f.lineno <= ln <= f.end_lineno]
        return min(calon, key=lambda f: f.end_lineno - f.lineno).name if calon else "<modul>"

    out = []
    for n in ast.walk(t):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute):
            bn = nama(n.func.value)
            if n.func.attr == "today" and bn and (bn in date_n | dt_n or bn.endswith((".date", ".datetime"))):
                out.append((path.name, terdalam(n.lineno), n.lineno))
    for n in ast.walk(t):
        if isinstance(n, ast.Constant) and isinstance(n.value, str) and "SELECT" in n.value.upper() \
                and re.search(r"\bCURRENT_DATE\b", n.value):
            out.append((path.name, "SQL", n.lineno))
    return out


def test_hanya_situs_cadangan_yang_diizinkan():
    semua = [x for p in BERKAS for x in _situs_utc(p)]
    liar = [x for x in semua if (x[0], x[1]) not in DIIZINKAN]
    assert liar == [], liar
    # daftar izin tidak basi: tiap entri memang masih ada (kalau sudah bersih, hapus dari DIIZINKAN)
    assert {(x[0], x[1]) for x in semua} == DIIZINKAN


def test_penjaga_bisa_merah(tmp_path):
    f = tmp_path / "contoh.py"
    f.write_text("from datetime import date as _d\nasync def f():\n    return _d.today()\n"
                 "Q = \"SELECT 1 FROM x WHERE due_date < CURRENT_DATE\"\n")
    got = _situs_utc(f)
    assert ("contoh.py", "f", 3) in got and ("contoh.py", "SQL", 4) in got


def _wajib(fn, nama):
    p = inspect.signature(fn).parameters[nama]
    return p.default is inspect.Parameter.empty


def test_today_wajib_di_semua_fungsi_periode():
    from app.services.unified_agent import clarification_slots as CS
    from app.services.unified_agent import driver_deltas as DD
    from app.services.unified_agent import period_resolver as PR
    from app.services.unified_agent import projection_engine as PE
    from app.services.unified_agent import tool_executor as TE

    assert _wajib(PR.resolve_period, "today")
    assert inspect.signature(PR.resolve_period).parameters["today"].kind is inspect.Parameter.KEYWORD_ONLY
    assert _wajib(CS.try_fill_period_slot, "today")
    assert _wajib(DD._resolve_periods, "today")
    assert _wajib(PE._last_two_complete_months, "today")
    assert _wajib(PE.execute_gross_profit_projection, "today")
    assert _wajib(TE.ToolExecutor._build_chart_spec, "hari_ini")
    with pytest.raises(TypeError):
        PR.resolve_period("bulan ini")


def test_bulan_ini_tgl_1_menurut_tanggal_bisnis():
    from app.services.unified_agent import period_resolver as PR

    r = PR.resolve_period("laporan bulan ini", today=date(2026, 10, 1))   # 1 Okt 01:00 WIB = 30 Sep UTC
    assert (r["start_date"], r["end_date"]) == ("2026-10-01", "2026-10-31"), r


def test_orchestrator_mengoper_today_dari_tanggal_tenant():
    t = ast.parse((UA / "orchestrator.py").read_text())
    target = {"_resolve_period": "today", "try_fill_period_slot": "today", "execute_gross_profit_projection": "today"}
    ketemu = 0
    for c in ast.walk(t):
        if isinstance(c, ast.Call) and isinstance(c.func, ast.Name) and c.func.id in target:
            kw = [k for k in c.keywords if k.arg == target[c.func.id]]
            assert kw and "_hari_ini_tenant(context.tenant_id)" in ast.unparse(kw[0].value), (c.lineno, ast.unparse(c))
            ketemu += 1
    assert ketemu >= 4, ketemu


def test_sql_jatuh_tempo_bot_pakai_tanggal_bisnis():
    src = (UA / "orchestrator.py").read_text()
    assert src.count("due_date < tanggal_bisnis($1)") == 2
    assert src.count("(tanggal_bisnis($1) - due_date) AS days_overdue") == 2
    assert "tanggal_bisnis($1) - INTERVAL '90 days'" in (UA / "restock_priority.py").read_text()
    assert "tanggal_bisnis($1) - INTERVAL '7 days'" in (APP / "services/financial_intelligence.py").read_text()
