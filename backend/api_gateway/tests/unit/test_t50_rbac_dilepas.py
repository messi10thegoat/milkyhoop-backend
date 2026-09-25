"""#50 — RBACMiddleware (tier langganan) DILEPAS; izin = satu sumber (25 Sep 2026).

Latar (diukur 25 Sep, master 3f7459e8):
  - RBACMiddleware menilai `User.role` dari JWT = PLAN TIER (FREE/USER/OWNER/ADMIN),
    bukan peran tim. Signup + undangan selalu menulis ADMIN; prod 8 ADMIN + 1 FREE
    (akun uji). Ia tak pernah menolak pengguna nyata -> ilusi perlindungan.
  - Dari 1086 rute app, 27 (metode+jalur) ber-tier USER: /api/customers/*,
    /api/kasbank/*, /api/products/{search,recent-sales,health}. /api/admin/ dan
    /api/tenant/{settings,users,billing} = 0 rute.
  - Banding dispatch SEBELUM (RBAC -> Permission) vs SESUDAH (Permission saja),
    SEMUA rute x tier {ADMIN, FREE} x peran {OWNER, COLLABORATOR, VIEWER} = 6516
    kasus, policy engine tiruan: tier ADMIN 0 beda; tier FREE beda HANYA di 27 rute
    ini, 403 -> keputusan izin peran (skrip /root/logs/banding_rbac.py).

Yang dijaga di sini:
  (1) tiap rute yang dulu ber-tier USER kini punya pola izin EKSPLISIT
      (ROUTE_PERMISSIONS dengan metodenya), kecuali probe /health (SKIP);
  (2) keputusan sesudah pelepasan = keputusan izin peran, untuk tier FREE juga:
      peran tanpa izin tetap 403, peran dengan izin 200;
  (3) RBACMiddleware tak dipasang lagi di main.py.
"""
import asyncio
import re
from pathlib import Path

import pytest
from starlette.applications import Starlette
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from app.middleware import permission_middleware as PM

DISINI = Path(__file__).parent
INVENTARIS = DISINI / "inventaris_rute_tier_user.txt"
MAIN = DISINI.parents[1] / "app" / "main.py"


def _rute():
    baris = [
        b.strip()
        for b in INVENTARIS.read_text(encoding="utf-8").splitlines()
        if b.strip() and not b.startswith("#")
    ]
    out = []
    for b in baris:
        m, _, p = b.partition(" ")
        out.append((m, re.sub(r"\{[^}]+\}", "XX", p)))
    return out


def test_inventaris_utuh():
    r = _rute()
    # Gagal-keras bila berkas kosong/terpotong: nol rute = hijau palsu.
    assert len(r) == 27
    assert ("GET", "/api/customers/XX") in r
    assert ("GET", "/api/kasbank/accounts") in r


def test_tiap_rute_bertier_punya_pola_eksplisit():
    mw = PM.PermissionMiddleware(app=None)
    kurang = []
    for m, p in _rute():
        if p.endswith("/health"):
            assert any(s.match(p) for s in mw._compiled_skip), p
            continue
        if mw._find_permission(p, m) is None:
            kurang.append(f"{m} {p}")
    assert kurang == [], f"rute bekas tier USER tanpa pola izin: {kurang}"


class _Ctx:
    def __init__(self, role):
        self.membership_active = True
        self.business_role_id = "r-" + role
        self.business_role_code = role
        self.visibility_levels = []
        self.approval_limit = None


class _Eng:
    """VIEWER: baca customer/kas_bank/item. KASIR: tanpa customer sama sekali."""

    async def get_user_context(self, user_id, tenant_id, subscription_role):
        return _Ctx(user_id)

    async def can(self, ctx, action, module):
        r = ctx.business_role_code
        if r == "OWNER":
            return True
        if r == "VIEWER":
            return action == "R" and module in ("customer", "kas_bank", "item")
        return False


class _SetUser(BaseHTTPMiddleware):
    async def dispatch(self, req, nxt):
        req.state.user = {
            "user_id": req.headers["x-peran"],
            "tenant_id": "t",
            "role": req.headers["x-tier"],
        }
        return await nxt(req)


async def _ok(req):
    return PlainTextResponse("ok")


@pytest.fixture
def klien(monkeypatch):
    monkeypatch.setattr(PM, "get_policy_engine", lambda: _Eng())
    a = Starlette(
        routes=[Route("/{p:path}", _ok, methods=["GET", "POST", "PUT", "PATCH", "DELETE"])]
    )
    a.add_middleware(PM.PermissionMiddleware)
    a.add_middleware(_SetUser)
    return TestClient(a)


def _kode(klien, m, p, peran, tier):
    return klien.request(m, p, headers={"x-peran": peran, "x-tier": tier}).status_code


@pytest.mark.parametrize("tier", ["FREE", "ADMIN"])
def test_keputusan_ikut_izin_peran_bukan_tier(klien, tier):
    for m, p in _rute():
        if p.endswith("/health"):
            continue
        mod, aksi = PM.PermissionMiddleware(app=None)._find_permission(p, m)
        # OWNER lolos semua; KASIR (tanpa izin) ditolak semua; VIEWER hanya baca.
        assert _kode(klien, m, p, "OWNER", tier) == 200, (m, p)
        assert _kode(klien, m, p, "KASIR", tier) == 403, (m, p)
        harap = 200 if (aksi == "R" and mod in ("customer", "kas_bank", "item")) else 403
        assert _kode(klien, m, p, "VIEWER", tier) == harap, (m, p, mod, aksi)


def test_viewer_tak_bisa_menulis_pelanggan(klien):
    # Kasus tunggal yang terbaca: dulu tier FREE 403 karena TIER; kini 403 karena IZIN.
    r = klien.request("PATCH", "/api/customers/abc", headers={"x-peran": "VIEWER", "x-tier": "FREE"})
    assert r.status_code == 403
    assert r.json()["code"] == "PERMISSION_DENIED"


def test_rbac_tier_tak_dipasang_lagi():
    t = MAIN.read_text(encoding="utf-8")
    assert "add_middleware(RBACMiddleware)" not in t
    assert "rbac_middleware import" not in t
    assert "add_middleware(PermissionMiddleware)" in t
