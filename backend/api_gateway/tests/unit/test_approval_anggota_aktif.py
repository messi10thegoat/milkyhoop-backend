"""Sisa audit izin usul D (26 Sep 2026) — anggota SUSPENDED tak bisa membatalkan pengajuan.

POST /api/approval-requests/{id}/cancel = WRITE_EXEMPT -> PermissionMiddleware tak
memeriksa keanggotaan aktif. Router approvals kini membawa
require_active_membership (pola dashboard): nonaktif -> 403 SEBELUM handler.
"""
import pytest
from fastapi import FastAPI, HTTPException
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.testclient import TestClient

from app.routers import approvals as AP
from app.services import role_resolution as RR
from app.services.role_resolution import require_active_membership

SUSPENDED = "11111111-1111-1111-1111-111111111111"
AKTIF = "22222222-2222-2222-2222-222222222222"
BATAL = "/api/approval-requests/00000000-0000-0000-0000-000000000001/cancel"


class _SetUser(BaseHTTPMiddleware):
    async def dispatch(self, req, nxt):
        req.state.user = {"user_id": req.headers["x-u"], "tenant_id": "t", "role": "ADMIN"}
        return await nxt(req)


class _Acq:
    async def __aenter__(self):
        return object()

    async def __aexit__(self, *a):
        return False


class _Pool:
    def acquire(self):
        return _Acq()


@pytest.fixture
def ctx(monkeypatch):
    sampai = []

    async def pool():
        return _Pool()

    async def peran(conn, user_id, tenant_id):
        if user_id == SUSPENDED:
            raise HTTPException(status_code=403, detail={"error_code": "MEMBERSHIP_INACTIVE"})
        return "STAFF"

    async def handler_pool():
        sampai.append("handler")
        raise HTTPException(status_code=418, detail="handler tercapai")

    import app.services.db_pool as DBP
    monkeypatch.setattr(DBP, "get_db_pool", pool)
    monkeypatch.setattr(RR, "resolve_business_role", peran)
    monkeypatch.setattr(AP, "get_pool", handler_pool)
    app = FastAPI()
    app.include_router(AP.router, prefix="/api")
    app.add_middleware(_SetUser)
    return TestClient(app), sampai


def test_suspended_batal_403_sebelum_handler(ctx):
    k, sampai = ctx
    r = k.post(BATAL, json={"reason": "x"}, headers={"x-u": SUSPENDED})
    assert r.status_code == 403
    assert r.json()["detail"]["error_code"] == "MEMBERSHIP_INACTIVE"
    assert sampai == []


def test_aktif_batal_sampai_handler(ctx):
    k, sampai = ctx
    r = k.post(BATAL, json={"reason": "x"}, headers={"x-u": AKTIF})
    assert r.status_code == 418
    assert sampai == ["handler"]


def test_pagar_di_level_router_semua_rute():
    deps = [d.dependency for d in AP.router.dependencies]
    assert require_active_membership in deps
    for r in AP.router.routes:
        assert any(d.call is require_active_membership for d in r.dependant.dependencies), r.path
