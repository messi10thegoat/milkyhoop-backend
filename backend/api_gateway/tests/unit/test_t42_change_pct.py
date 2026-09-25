"""#42 -- calc_change_pct dashboard (BUG-005 sisi BE, 25 Sep 2026).

Dulu: prev 0 -> 100.0 (karangan), 0/0 -> 0.0, prev negatif -> tanda terbalik
(laba rugi: rugi 100 -> laba 50 tampil -150%). FE r144 menghitung sendiri untuk
dashboard/all, tapi pembaca lain (SalesOverview profit_change_pct,
PurchaseOverview hutang, DashboardPanel kartu) masih memakai angka BE; semuanya
sudah aman terhadap null (`!= null`).
"""
import ast
from pathlib import Path

import pytest

from app.routers import dashboard as D


@pytest.mark.parametrize("cur,prev,harap", [
    (0, 0, 0.0),
    (500, 500, 0.0),
    (5_000_000, 0, None),          # baru: tak ada dasar persen
    (-200, 0, None),
    (150, 100, 50.0),
    (50, 100, -50.0),
    (0, 100, -100.0),
    (50, -100, 150.0),             # rugi 100 -> laba 50 = membaik
    (-150, -100, -50.0),           # rugi membesar = memburuk
    (1, 3, -66.7),
])
def test_persen_perubahan(cur, prev, harap):
    assert D.calc_change_pct(cur, prev) == harap


def test_model_respons_menerima_null():
    for nama, medan in (("LabaRugiSummary", "profit_change_pct"), ("PiutangSummary", "change_pct"),
                        ("HutangSummary", "change_pct"), ("KasBankSummary", "change_pct")):
        m = getattr(D, nama)  # nama salah -> AttributeError (bukan lewat diam-diam)
        assert medan in m.model_fields
        from pydantic import TypeAdapter
        assert TypeAdapter(m.model_fields[medan].annotation).validate_python(None) is None
        assert m.model_fields[medan].is_required() is False


def test_semua_pemanggil_memakai_fungsi_ini():
    src = Path(D.__file__).read_text()
    pohon = ast.parse(src)
    panggil = [n for n in ast.walk(pohon) if isinstance(n, ast.Call)
               and getattr(n.func, "id", None) == "calc_change_pct"]
    assert len(panggil) == 4
    # tak ada rumus persen tandingan "* 100" di samping prev di berkas ini
    assert "return 100.0" not in src
