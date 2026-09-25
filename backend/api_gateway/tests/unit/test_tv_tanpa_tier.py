"""Audit izin usul A (25 Sep 2026) — TenantValidationMiddleware tanpa pengecualian tier ADMIN.

Latar (diukur, master 8c27c189):
  - Middleware dulu meloloskan `user["role"] == "ADMIN"` ke tenant MANA PUN.
    `role` = PLAN TIER dari JWT; signup + undangan selalu ADMIN (prod: 8 ADMIN,
    1 FREE = akun uji) -> validasi tenant-di-URL mati untuk semua pengguna nyata.
  - Rute ber-tenant di URL: POST /api/tenant/{id}/chat (terautentikasi) dan
    GET /api/tenant/{id}/info (AuthMiddleware melewatinya tanpa user -> tak
    terdampak). /api/tenant/profile* = rute, bukan tenant.
  - Pemakaian: access log uvicorn 209 arsip (~3 Sep..25 Sep) POST
    /api/tenant/*/chat = 0. (logger.info middleware TIDAK tercetak di log -> nol
    "Admin access granted" bukan bukti; pengukur yang dipakai = access log.)
"""
import pytest
from starlette.applications import Starlette
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from app.middleware.tenant_validation_middleware import TenantValidationMiddleware


class _SetUser(BaseHTTPMiddleware):
    async def dispatch(self, req, nxt):
        t = req.headers.get("x-tenant")
        if t:
            req.state.user = {"user_id": "u1", "tenant_id": t, "role": req.headers.get("x-tier", "ADMIN")}
        return await nxt(req)


async def _ok(req):
    return PlainTextResponse("ok")


@pytest.fixture
def klien():
    a = Starlette(routes=[Route("/{p:path}", _ok, methods=["GET", "POST", "PATCH", "DELETE"])])
    a.add_middleware(TenantValidationMiddleware)
    a.add_middleware(_SetUser)
    return TestClient(a)


def _req(k, m, p, tenant=None, tier="ADMIN"):
    h = {"x-tier": tier}
    if tenant:
        h["x-tenant"] = tenant
    return k.request(m, p, headers=h)


@pytest.mark.parametrize("tier", ["ADMIN", "OWNER", "USER", "FREE", ""])
def test_tier_apa_pun_ditolak_ke_tenant_lain(klien, tier):
    r = _req(klien, "POST", "/api/tenant/grapgrap-manado/chat", "kaos-biru-konveksi", tier)
    assert r.status_code == 403
    assert r.json()["code"] == "TENANT_MISMATCH"


@pytest.mark.parametrize("tier", ["ADMIN", "FREE"])
def test_tenant_sendiri_lolos(klien, tier):
    assert _req(klien, "POST", "/api/tenant/kaos-biru-konveksi/chat", "kaos-biru-konveksi", tier).status_code == 200


def test_tanpa_user_diteruskan_ke_lapis_lain(klien):
    # /info publik: AuthMiddleware meneruskan tanpa user -> jangan diblok di sini.
    assert _req(klien, "GET", "/api/tenant/grapgrap-manado/info").status_code == 200


def test_rute_profile_bukan_tenant(klien):
    assert _req(klien, "POST", "/api/tenant/profile/logo", "kaos-biru-konveksi").status_code == 200
    assert _req(klien, "GET", "/api/tenant/profile/logo/x.png", "kaos-biru-konveksi").status_code == 200


def test_rute_tanpa_tenant_di_url_tak_disentuh(klien):
    assert _req(klien, "GET", "/api/customers", "kaos-biru-konveksi").status_code == 200
