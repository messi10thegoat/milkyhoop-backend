"""Audit izin usul B (25 Sep 2026) — GET /api/auth/profile/{user_id} hanya diri sendiri.

Latar (diukur, master 8c27c189): rute SKIP izin (^/api/auth), AuthMiddleware
menuntut login, tapi handler melayani user_id APA PUN -> login mana pun membaca
email/nama/role pengguna lain lintas tenant asal tahu UUID. FE: 0 pemakai.

Kontrak: orang lain -> 404 dengan badan IDENTIK dengan UUID tak ada, dan layanan
auth TIDAK dipanggil untuk orang lain (tak ada oracle keberadaan / waktu jawab).
"""
import pytest
from fastapi import FastAPI
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.testclient import TestClient

from app.routers import auth as A

SAYA = "11111111-1111-1111-1111-111111111111"
LAIN = "22222222-2222-2222-2222-222222222222"
HANTU = "33333333-3333-3333-3333-333333333333"


class _Auth:
    def __init__(self):
        self.dipanggil = []

    async def get_user_profile(self, uid):
        self.dipanggil.append(uid)
        if uid in (SAYA, LAIN):
            return {"success": True, "user_id": uid, "email": uid[:4] + "@x.id",
                    "name": "N", "username": "u", "role": "ADMIN"}
        return {"success": False, "message": "User not found: " + uid}

    async def disconnect(self):
        pass


class _SetUser(BaseHTTPMiddleware):
    async def dispatch(self, req, nxt):
        u = req.headers.get("x-user")
        if u:
            req.state.user = {"user_id": u, "tenant_id": "t", "role": "ADMIN"}
        return await nxt(req)


@pytest.fixture
def ctx(monkeypatch):
    palsu = _Auth()
    monkeypatch.setattr(A, "auth_client", palsu)
    app = FastAPI()
    app.include_router(A.router, prefix="/api/auth")
    app.add_middleware(_SetUser)
    return TestClient(app), palsu


def _get(k, uid, pemanggil=SAYA):
    h = {"x-user": pemanggil} if pemanggil else {}
    return k.get(f"/api/auth/profile/{uid}", headers=h)


def test_diri_sendiri_dilayani(ctx):
    k, palsu = ctx
    r = _get(k, SAYA)
    assert r.status_code == 200
    assert r.json()["data"]["user_id"] == SAYA
    assert palsu.dipanggil == [SAYA]


def test_orang_lain_404_tanpa_memanggil_layanan(ctx):
    k, palsu = ctx
    r = _get(k, LAIN)
    assert r.status_code == 404
    assert "@" not in r.text and LAIN not in r.text
    assert palsu.dipanggil == []


def test_orang_lain_identik_dengan_uuid_tak_ada(ctx):
    k, _ = ctx
    ada, hantu = _get(k, LAIN), _get(k, HANTU)
    assert (ada.status_code, ada.content) == (hantu.status_code, hantu.content)
    assert ada.json() == {"detail": A.PROFIL_TAK_DITEMUKAN}


def test_diri_sendiri_tak_ada_jawaban_sama(ctx):
    # Layanan auth berkata "tak ada" (pesan berisi UUID) -> pesan tetap, bukan pesan layanan.
    k, _ = ctx
    r = _get(k, HANTU, pemanggil=HANTU)
    assert r.status_code == 404
    assert r.json() == {"detail": A.PROFIL_TAK_DITEMUKAN}


def test_tanpa_user_404(ctx):
    k, palsu = ctx
    assert _get(k, SAYA, pemanggil=None).status_code == 404
    assert palsu.dipanggil == []
