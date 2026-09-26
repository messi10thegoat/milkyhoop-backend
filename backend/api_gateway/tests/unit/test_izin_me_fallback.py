"""GET /api/permissions/me — fallback saat PolicyEngine BELUM ter-inisialisasi (26 Sep 2026).

Dulu resolve_business_role tak diimpor (sejak c3a27416) -> NameError -> 500 tepat di keadaan terdegradasi.
Tes MEMAKSA fallback (get_policy_engine melempar RuntimeError not initialized)."""
import os
from types import SimpleNamespace

os.environ.setdefault("OPENAI_API_KEY", "sk-boneka-unit-test-tanpa-jaringan")

import pytest  # noqa: E402

from app.routers import team_members as TM  # noqa: E402
from app.services import policy_engine_client as PEC  # noqa: E402
from app.services import role_resolution as RR  # noqa: E402


class _K:
    def __init__(self):
        self.q = []

    async def fetch(self, sql, *a):
        self.q.append((sql, a))
        return [{"module": "SALES_ORDER", "actions": ["R", " C"]}]


class _Acq:
    def __init__(self, c):
        self.c = c

    async def __aenter__(self):
        return self.c

    async def __aexit__(self, *a):
        return False


@pytest.mark.asyncio
async def test_fallback_tanpa_policy_engine_memakai_peran_kanonik(monkeypatch):
    k = _K()

    def belum():
        raise RuntimeError("PolicyEngine not initialized")

    async def peran(conn, uid, tid):
        assert (uid, tid) == ("u1", "t")
        return "SALES"

    async def pool():
        return SimpleNamespace(acquire=lambda: _Acq(k))

    async def fitur(conn, tid):
        return ["x"]

    monkeypatch.setattr(PEC, "get_policy_engine", belum)
    monkeypatch.setattr(RR, "resolve_business_role", peran)
    monkeypatch.setattr(TM, "get_pool", pool)
    monkeypatch.setattr(TM, "fitur_aktif", fitur)
    req = SimpleNamespace(state=SimpleNamespace(user={"user_id": "u1", "tenant_id": "t"}))
    r = await TM.get_my_permissions(req)
    assert r["role_code"] == "SALES"
    assert r["effective_permissions"] == {"SALES_ORDER": {"actions": ["R", "C"], "source": "role"}}
    assert r["features"] == ["x"]
    sql, a = k.q[0]
    assert "tenant_id IN (\x27__SYSTEM__\x27, $2)" in sql and a == ("SALES", "t")
