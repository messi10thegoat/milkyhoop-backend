"""Audit izin usul D (25 Sep 2026) — tolak & batal pengajuan persetujuan.

Latar (diukur, master 8c27c189): POST .../approve = approval_inbox:A, tetapi
.../reject dan .../cancel tanpa pola -> default-tertutup (non-owner 403
PERMISSION_UNMAPPED). Akibat: approver staf bisa SETUJU tapi tak bisa TOLAK;
pengaju staf tak bisa membatalkan pengajuannya sendiri. approval_requests = 0
baris (laten). Handler sudah memeriksa: reject -> approver level; cancel ->
requested_by == pemanggil (lain 403).
"""
import pytest
from starlette.applications import Starlette
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from app.middleware import permission_middleware as PM

TOLAK = "/api/approval-requests/abc/reject"
SETUJU = "/api/approval-requests/abc/approve"
BATAL = "/api/approval-requests/abc/cancel"


class _Ctx:
    def __init__(self, role):
        self.membership_active = True
        self.business_role_id = "r-" + role
        self.business_role_code = role
        self.visibility_levels = []
        self.approval_limit = None


class _Eng:
    async def get_user_context(self, user_id, tenant_id, subscription_role):
        return _Ctx(user_id)

    async def can(self, ctx, action, module):
        r = ctx.business_role_code
        if r == "OWNER":
            return True
        if r == "APPROVER":
            return module == "approval_inbox" and action == "A"
        return False


class _SetUser(BaseHTTPMiddleware):
    async def dispatch(self, req, nxt):
        req.state.user = {"user_id": req.headers["x-peran"], "tenant_id": "t", "role": "ADMIN"}
        return await nxt(req)


async def _ok(req):
    return PlainTextResponse("ok")


@pytest.fixture
def klien(monkeypatch):
    monkeypatch.setattr(PM, "get_policy_engine", lambda: _Eng())
    a = Starlette(routes=[Route("/{p:path}", _ok, methods=["GET", "POST"])])
    a.add_middleware(PM.PermissionMiddleware)
    a.add_middleware(_SetUser)
    return TestClient(a)


def _post(k, p, peran):
    return k.post(p, headers={"x-peran": peran})


def test_tolak_izin_sama_dengan_setuju():
    mw = PM.PermissionMiddleware(app=None)
    assert mw._find_permission(TOLAK, "POST") == ("approval_inbox", "A")
    assert mw._find_permission(TOLAK, "POST") == mw._find_permission(SETUJU, "POST")


def test_approver_staf_bisa_tolak_dan_setuju(klien):
    assert _post(klien, SETUJU, "APPROVER").status_code == 200
    assert _post(klien, TOLAK, "APPROVER").status_code == 200


def test_tanpa_izin_approval_tak_bisa_tolak(klien):
    r = _post(klien, TOLAK, "KASIR")
    assert r.status_code == 403
    assert r.json()["code"] == "PERMISSION_DENIED"


def test_batal_diteruskan_ke_handler_untuk_staf(klien):
    # Middleware tak memakai izin modul untuk batal; penjaga = requested_by di handler.
    assert _post(klien, BATAL, "KASIR").status_code == 200


def test_batal_terdaftar_exempt_dengan_alasan_pengaju():
    alasan = [a for p, a in PM.WRITE_EXEMPT if "approval-requests" in p]
    assert len(alasan) == 1 and "PENGAJU" in alasan[0]


def test_pola_batal_sempit():
    # Pengecualian hanya untuk /cancel, bukan seluruh approval-requests.
    mw = PM.PermissionMiddleware(app=None)
    assert any(x.match(BATAL) for x in mw._compiled_write_exempt)
    for p in (TOLAK, SETUJU, "/api/approval-requests/abc/cancel/x", "/api/approval-requests"):
        assert not any(x.match(p) for x in mw._compiled_write_exempt), p
