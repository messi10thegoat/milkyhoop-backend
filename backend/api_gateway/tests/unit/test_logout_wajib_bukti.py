"""Audit sesi (26 Sep 2026) — logout wajib BUKTI identitas; sesi dari JWT; logout-all sungguhan.

Terukur: POST /api/auth/logout ada di public_paths dan mempercayai ?user_id ->
tanpa token siapa pun memaksa-keluar pengguna mana pun (Redis revoke_device /
revoke_all) dan, dengan logout_all_devices=true, mencabut SEMUA refresh token
korban (auth_service Logout where userId saja). FE (utils/auth.ts:184)
memanggil TANPA Authorization: hanya ?user_id + refresh_token di body.
/api/auth/sessions GET/DELETE memakai ?user_id; /api/session/logout(-all) stub.
"""
import hashlib

import pytest
from fastapi import FastAPI
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.testclient import TestClient

from app.routers import auth as A
from app.routers import session as SE
from app.services import session_manager as SMmod

KORBAN = "00000000-0000-0000-0000-0000000000aa"
SAYA = "00000000-0000-0000-0000-0000000000bb"
RT_SAYA = "refresh-token-milik-saya"
JWT_SAH = "jwt-sah-saya"


class _Catat:
    def __init__(self):
        self.redis = []
        self.auth = []


@pytest.fixture
def uji(monkeypatch):
    c = _Catat()

    class _SM:
        def revoke_device(self, uid, dev):
            c.redis.append(("device", uid, dev))
            return True

        def revoke_all(self, uid):
            c.redis.append(("all", uid))
            return True

    class _Auth:
        async def validate_token(self, tok):
            return {"valid": tok == JWT_SAH, "user_id": SAYA if tok == JWT_SAH else None}

        async def logout(self, user_id, refresh_token=None, logout_all_devices=False):
            c.auth.append((user_id, refresh_token, logout_all_devices))
            return {"success": True, "revoked_tokens": 1}

        async def list_active_sessions(self, uid):
            c.auth.append(("list", uid))
            return {"success": True, "sessions": [], "total": 0}

        async def revoke_session(self, sid, uid):
            c.auth.append(("revoke", sid, uid))
            return {"success": True}

    class _Pool:
        async def fetchval(self, sql, h):
            assert "revoked_at IS NULL" in sql and "expires_at > now()" in sql
            return SAYA if h == hashlib.sha256(RT_SAYA.encode()).hexdigest() else None

    async def pool():
        return _Pool()

    async def log(**k):
        pass

    sm = _SM()
    monkeypatch.setattr(A, "session_manager", sm)
    monkeypatch.setattr(SMmod, "session_manager", sm)
    # session.py mengimpor lewat jalur `backend.api_gateway...` = objek modul LAIN
    import backend.api_gateway.app.services.session_manager as SMmod2
    monkeypatch.setattr(SMmod2, "session_manager", sm)
    monkeypatch.setattr(A, "auth_client", _Auth())
    monkeypatch.setattr(SE, "auth_client", _Auth())
    monkeypatch.setattr(A, "get_pool", pool)
    monkeypatch.setattr(A, "log_auth_event", log)
    return c


def _klien_publik():
    app = FastAPI()
    app.include_router(A.router, prefix="/api/auth")
    return TestClient(app)


class _SetUser(BaseHTTPMiddleware):
    async def dispatch(self, req, nxt):
        u = req.headers.get("x-u")
        if u:
            req.state.user = {"user_id": u, "tenant_id": "t"}
        return await nxt(req)


def _klien_login():
    app = FastAPI()
    app.include_router(A.router, prefix="/api/auth")
    app.include_router(SE.router)
    app.add_middleware(_SetUser)
    return TestClient(app)


# ---------- P1 /api/auth/logout ----------

def test_alur_fe_sekarang_tetap_logout(uji):
    """FE: ?user_id + refresh_token di body, TANPA Authorization."""
    r = _klien_publik().post(f"/api/auth/logout?user_id={SAYA}",
                             json={"refresh_token": RT_SAYA, "logout_all_devices": False})
    assert r.status_code == 200, r.text
    assert uji.redis == [("device", SAYA, "web")]
    assert uji.auth == [(SAYA, RT_SAYA, False)]


@pytest.mark.parametrize("badan", [
    {"logout_all_devices": True},
    {"refresh_token": "tebakan", "logout_all_devices": True},
    {"refresh_token": "tebakan", "logout_all_devices": False},
    {},
])
def test_tanpa_bukti_401_nol_pencabutan(uji, badan):
    r = _klien_publik().post(f"/api/auth/logout?user_id={KORBAN}", json=badan)
    assert r.status_code == 401, r.text
    assert r.json()["detail"]["code"] == "LOGOUT_UNPROVEN"
    assert uji.redis == [] and uji.auth == []


def test_user_id_query_diabaikan_pakai_identitas_terbukti(uji):
    r = _klien_publik().post(f"/api/auth/logout?user_id={KORBAN}",
                             json={"refresh_token": RT_SAYA, "logout_all_devices": True})
    assert r.status_code == 200
    assert uji.redis == [("all", SAYA)]
    assert uji.auth == [(SAYA, RT_SAYA, True)]
    assert all(KORBAN not in str(x) for x in uji.redis + uji.auth)


def test_bearer_sah_cukup(uji):
    r = _klien_publik().post("/api/auth/logout", json={"logout_all_devices": True},
                             headers={"Authorization": f"Bearer {JWT_SAH}"})
    assert r.status_code == 200
    assert uji.redis == [("all", SAYA)]


def test_bearer_palsu_tanpa_refresh_401(uji):
    r = _klien_publik().post("/api/auth/logout", json={"logout_all_devices": True},
                             headers={"Authorization": "Bearer palsu"})
    assert r.status_code == 401 and uji.redis == [] and uji.auth == []


# ---------- P2 /api/auth/sessions ----------

def test_daftar_sesi_dari_jwt_bukan_query(uji):
    r = _klien_login().get(f"/api/auth/sessions?user_id={KORBAN}", headers={"x-u": SAYA})
    assert r.status_code == 200
    assert uji.auth == [("list", SAYA)]


def test_cabut_sesi_dari_jwt_bukan_query(uji):
    r = _klien_login().delete(f"/api/auth/sessions/s-1?user_id={KORBAN}", headers={"x-u": SAYA})
    assert r.status_code == 200
    assert uji.auth == [("revoke", "s-1", SAYA)]


def test_sesi_tanpa_jwt_401(uji):
    assert _klien_login().get(f"/api/auth/sessions?user_id={KORBAN}").status_code == 401
    assert uji.auth == []


# ---------- P3 /api/session ----------

def test_logout_all_mencabut_sungguhan(uji):
    r = _klien_login().post("/api/session/logout-all", headers={"x-u": SAYA})
    assert r.status_code == 200, r.text
    assert uji.redis == [("all", SAYA)]
    assert uji.auth == [(SAYA, None, True)]


def test_logout_satu_sesi_diparkir(uji):
    r = _klien_login().post("/api/session/logout?session_id=x", headers={"x-u": SAYA})
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "FEATURE_NOT_AVAILABLE"
    assert uji.redis == [] and uji.auth == []
