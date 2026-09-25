"""Audit WRITE_EXEMPT I4 (26 Sep 2026) — "anggota aktif" = baris peran ADA dan aktif.

Dulu cek hanya `ctx.membership_active`, yang BAWAANNYA True dan baru False bila
baris peran ADA tapi nonaktif. Anggota yang DIHAPUS dari tim (DELETE
user_tenant_roles; token hidup s/d 7 hari) tak punya baris -> lolos; galat DB
di get_user_context juga lolos. Rute exempt terdampak: intake (upload/reject/
confirm/execute/batch/retry), /api/documents/upload + attach, dan
/api/uploads/document (dulu TANPA cek sama sekali). events.py membebaskan tier
JWT ADMIN (= semua pengguna nyata). Terukur: 9/9 pengguna punya baris -> 0 terkunci.
"""
import re
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.testclient import TestClient

from app.services import policy_engine_client as PEC
from app.services.policy_engine_client import anggota_aktif, UserContext

APP = Path(__file__).parents[2] / "app"
USER = "00000000-0000-0000-0000-00000000000a"


def _ctx(aktif=True, role_id="r-1", code="STAFF"):
    c = UserContext(user_id=USER, tenant_id="t", subscription_role="ADMIN", visibility_levels=[])
    c.membership_active = aktif
    c.business_role_id = role_id
    c.business_role_code = code
    return c


def test_definisi():
    assert anggota_aktif(_ctx()) is True
    assert anggota_aktif(_ctx(role_id=None, code=None)) is False  # DIHAPUS / galat DB
    assert anggota_aktif(_ctx(aktif=False)) is False  # SUSPENDED
    assert anggota_aktif(UserContext(user_id=USER, tenant_id="t", subscription_role="ADMIN")) is False  # bawaan


class _Eng:
    def __init__(self, ctx):
        self.ctx = ctx

    async def get_user_context(self, *a, **k):
        return self.ctx

    async def can(self, *a):
        return True


@pytest.mark.asyncio
@pytest.mark.parametrize("modul,fungsi", [
    ("app.routers.document_intake", "_require_active_member"),
    ("app.routers.documents", "_require_active_member_docs"),
])
async def test_penjaga_router_menolak_tanpa_baris(monkeypatch, modul, fungsi):
    import importlib
    m = importlib.import_module(modul)
    monkeypatch.setattr(PEC, "get_policy_engine", lambda: _Eng(_ctx(role_id=None, code=None)))
    req = SimpleNamespace(state=SimpleNamespace(user={"user_id": USER, "tenant_id": "t", "role": "ADMIN"}))
    with pytest.raises(HTTPException) as e:
        await getattr(m, fungsi)(req)
    assert e.value.status_code == 403


@pytest.mark.asyncio
async def test_penjaga_router_meloloskan_anggota_aktif(monkeypatch):
    from app.routers import document_intake as DI
    monkeypatch.setattr(PEC, "get_policy_engine", lambda: _Eng(_ctx()))
    req = SimpleNamespace(state=SimpleNamespace(user={"user_id": USER, "tenant_id": "t", "role": "ADMIN"}))
    await DI._require_active_member(req)


class _SetUser(BaseHTTPMiddleware):
    async def dispatch(self, req, nxt):
        req.state.user = {"user_id": USER, "tenant_id": "kaos-biru-konveksi", "role": "ADMIN"}
        return await nxt(req)


def test_uploads_document_anggota_dihapus_403_nol_tulis(monkeypatch):
    from app.routers import uploads as U
    tulis = []

    async def simpan(*a, **k):
        tulis.append("objek")

    monkeypatch.setattr(PEC, "get_policy_engine", lambda: _Eng(_ctx(role_id=None, code=None)))
    monkeypatch.setattr(U, "simpan_objek_unggahan", simpan)
    app = FastAPI()
    app.include_router(U.router)
    app.add_middleware(_SetUser)
    r = TestClient(app).post("/api/uploads/document", files={"file": ("a.png", b"\x89PNG\r\n\x1a\nxx", "image/png")})
    assert r.status_code == 403, r.text
    assert tulis == []


def test_events_tier_admin_tak_membebaskan():
    from app.routers import events as E
    assert E._revocation_from_ctx(_ctx(role_id=None, code=None), "ADMIN") == "MEMBERSHIP_INACTIVE"
    assert E._revocation_from_ctx(_ctx(), "ADMIN") is None


def test_tak_ada_cek_membership_active_telanjang_di_rute_exempt():
    """Kembali ke `if not x.membership_active:` = MERAH (penjaga lemah bawaan-True)."""
    for f in ("routers/document_intake.py", "routers/documents.py", "routers/uploads.py"):
        src = (APP / f).read_text(encoding="utf-8")
        assert not re.search(r"if not \w+\.membership_active", src), f


def test_uploads_document_anggota_aktif_tetap_menulis(monkeypatch):
    """Kontrol positif: penjaga yang menolak SEMUA orang juga akan lolos tes 403 di atas."""
    from app.routers import uploads as U
    tulis = []

    async def simpan(*a, **k):
        tulis.append("objek")

    monkeypatch.setattr(PEC, "get_policy_engine", lambda: _Eng(_ctx()))
    monkeypatch.setattr(U, "simpan_objek_unggahan", simpan)
    app = FastAPI()
    app.include_router(U.router)
    app.add_middleware(_SetUser)
    TestClient(app, raise_server_exceptions=False).post(
        "/api/uploads/document", files={"file": ("a.png", b"\x89PNG\r\n\x1a\nxx", "image/png")})
    assert tulis == ["objek"]
