"""F4 (26 Sep 2026): token tanpa klaim perangkat melewati kill switch + JWT_SECRET cadangan tertanam.

Diukur: /api/auth/refresh (akses dari gRPC tanpa device), /api/auth/switch-tenant (akses tanpa device + refresh JWT
TAK tersimpan), login tenant-berubah (refresh diganti JWT tak tersimpan), /api/auth/register (gRPC tanpa device).
Nilai cadangan 64-hex tertanam di 3 tempat kode + compose staging. Tes TIDAK memuat nilai itu (hanya hash uji).
"""
import hashlib
import inspect
import os
import re
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("OPENAI_API_KEY", "sk-boneka-unit-test-tanpa-jaringan")

import jwt  # noqa: E402
import pytest  # noqa: E402
from fastapi import FastAPI, HTTPException  # noqa: E402
from starlette.testclient import TestClient  # noqa: E402

from app.utils import rahasia_jwt as RJ  # noqa: E402
from app.middleware import auth_middleware as AM  # noqa: E402
from app.routers import auth as A  # noqa: E402

RAHASIA = "r" * 40
UID, DEV = "00000000-0000-0000-0000-0000000000a1", "d0000000-0000-0000-0000-000000000001"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", RAHASIA)


# ---------------- rahasia ----------------

@pytest.mark.parametrize("nilai", [None, "", "pendek"])
def test_rahasia_hilang_atau_pendek_gagal_keras(monkeypatch, nilai):
    if nilai is None:
        monkeypatch.delenv("JWT_SECRET", raising=False)
    else:
        monkeypatch.setenv("JWT_SECRET", nilai)
    with pytest.raises(RuntimeError):
        RJ.jwt_secret_wajib()


def test_rahasia_bocor_ditolak_lewat_hash(monkeypatch):
    bocor = "b" * 64
    monkeypatch.setattr(RJ, "_HASH_BOCOR", frozenset({hashlib.sha256(bocor.encode()).hexdigest()}))
    monkeypatch.setenv("JWT_SECRET", bocor)
    with pytest.raises(RuntimeError):
        RJ.jwt_secret_wajib()
    monkeypatch.setenv("JWT_SECRET", RAHASIA)
    assert RJ.jwt_secret_wajib() == RAHASIA


def test_daftar_hash_bocor_berisi_hash_bukan_nilai():
    assert len(RJ._HASH_BOCOR) >= 1 and all(re.fullmatch(r"[0-9a-f]{64}", h) for h in RJ._HASH_BOCOR)


def test_tak_ada_cadangan_tertanam_di_gateway():
    # runner unit hanya memasang api_gateway; salinan auth_service diperiksa skrip gerbang F4 (grep + diff)
    app_dir = Path(RJ.__file__).resolve().parents[1]
    for rel in ("routers/auth.py", "routers/signup.py", "services/auth_client.py"):
        teks = (app_dir / rel).read_text()
        assert not re.search(r'getenv\(\s*"JWT_SECRET"\s*,\s*"[^"]+"', teks), rel
        assert "jwt_secret_wajib" in teks, rel


def test_startup_gateway_memeriksa_rahasia_paling_awal():
    from app import main as M
    s = inspect.getsource(M.prisma_lifespan)
    assert s.index("_jwt_wajib()") < s.index("prisma.connect()")


# ---------------- middleware: gagal tertutup ----------------

class _Sesi:
    def __init__(self, sah=True):
        self.sah, self.panggil = sah, []

    def is_session_valid(self, u, t, d):
        self.panggil.append((u, t, d))
        return self.sah


@pytest.fixture
def mw(monkeypatch):
    st = {"klaim": {"device_id": DEV, "device_type": "web"}, "sesi": _Sesi()}

    async def validate(token):
        return {"valid": True, "user_id": UID, "tenant_id": "t", "role": "USER", **st["klaim"]}

    async def ada(uid):
        return True

    monkeypatch.setattr(AM, "auth_client", SimpleNamespace(validate_token=validate))
    monkeypatch.setattr(AM, "session_manager", st["sesi"])
    monkeypatch.setattr(AM, "_user_exists", ada)
    app = FastAPI()

    @app.get("/api/sales-orders")
    async def r():
        return {"ok": True}

    app.add_middleware(AM.AuthMiddleware)
    return TestClient(app), st


H = {"Authorization": "Bearer x.y.z"}


@pytest.mark.parametrize("klaim", [{}, {"device_id": DEV}, {"device_type": "web"}, {"device_id": "", "device_type": "web"}])
def test_mw_token_tanpa_klaim_perangkat_401(mw, klaim):
    k, st = mw
    st["klaim"] = klaim
    r = k.get("/api/sales-orders", headers=H)
    assert r.status_code == 401 and r.json()["code"] == "SESSION_INVALID" and r.json()["force_logout"] is True
    assert st["sesi"].panggil == []


def test_mw_sesi_diganti_401(mw):
    k, st = mw
    st["sesi"].sah = False
    r = k.get("/api/sales-orders", headers=H)
    assert r.status_code == 401 and r.json()["code"] == "SESSION_REPLACED"


def test_mw_klaim_lengkap_sesi_sah_lolos(mw):
    k, st = mw
    assert k.get("/api/sales-orders", headers=H).status_code == 200
    assert st["sesi"].panggil == [(UID, "web", DEV)]


# ---------------- /refresh ----------------

class _Acq:
    def __init__(self, c):
        self.c = c

    async def __aenter__(self):
        return self.c

    async def __aexit__(self, *a):
        return False


class _RConn:
    def __init__(self, dev=True, user=True):
        self.dev, self.user, self.q = dev, user, []

    async def fetchrow(self, sql, *a):
        self.q.append((sql, a))
        if "FROM user_devices" in sql:
            return {"id": DEV, "user_id": UID, "device_type": "web"} if self.dev else None
        raise AssertionError(sql[:60])

    async def fetchval(self, sql, *a):
        return 1 if self.user else None


def _akses_grpc(uid=UID):
    now = datetime.utcnow()
    return jwt.encode({"user_id": uid, "tenant_id": "t2", "role": "USER", "email": "e", "username": "u",
                       "token_type": "access", "iat": now, "exp": now + timedelta(days=7), "nbf": now},
                      RAHASIA, algorithm="HS256")


@pytest.fixture
def rf(monkeypatch):
    st = {"conn": _RConn(), "sesi": _Sesi(), "grpc": 0, "akses": _akses_grpc()}

    async def pool():
        return SimpleNamespace(acquire=lambda: _Acq(st["conn"]))

    async def refresh(tok):
        st["grpc"] += 1
        return {"success": True, "access_token": st["akses"], "refresh_token": tok, "user_id": UID}

    async def log(**k):
        return None

    monkeypatch.setattr(A, "get_pool", pool)
    monkeypatch.setattr(A, "session_manager", st["sesi"])
    monkeypatch.setattr(A, "auth_client", SimpleNamespace(refresh_token=refresh))
    monkeypatch.setattr(A, "log_auth_event", log)
    return st


def _req():
    return SimpleNamespace(headers={"user-agent": "t"}, client=SimpleNamespace(host="1.2.3.4"))


async def _refresh(tok="opak-tanpa-titik"):
    return await A.refresh_access_token(A.RefreshTokenRequest(refresh_token=tok), _req())


@pytest.mark.asyncio
async def test_refresh_membawa_klaim_perangkat_dari_user_devices(rf):
    r = await _refresh()
    d = r.data if hasattr(r, "data") else r["data"]
    m = jwt.decode(d["access_token"], RAHASIA, algorithms=["HS256"])
    assert (m["device_id"], m["device_type"], m["user_id"], m["tenant_id"]) == (DEV, "web", UID, "t2")
    sql, args = rf["conn"].q[0]
    assert args == (hashlib.sha256(b"opak-tanpa-titik").hexdigest(),) and "is_active" in sql
    assert rf["sesi"].panggil == [(UID, "web", DEV)]


@pytest.mark.asyncio
@pytest.mark.parametrize("keadaan,harap", [("tanpa_perangkat", "SESSION_INVALID"), ("sesi_diganti", "digantikan"),
                                           ("user_hilang", "User tidak ditemukan")])
async def test_refresh_ditolak_tanpa_memanggil_grpc(rf, keadaan, harap):
    if keadaan == "tanpa_perangkat":
        rf["conn"].dev = False
    elif keadaan == "sesi_diganti":
        rf["sesi"].sah = False
    else:
        rf["conn"].user = False
    with pytest.raises(HTTPException) as e:
        await _refresh()
    assert e.value.status_code == 401 and harap in str(e.value.detail) and rf["grpc"] == 0


@pytest.mark.asyncio
async def test_refresh_akses_grpc_milik_pengguna_lain_ditolak(rf):
    rf["akses"] = _akses_grpc("00000000-0000-0000-0000-0000000000ff")
    with pytest.raises(HTTPException) as e:
        await _refresh()
    assert e.value.status_code == 401


# ---------------- switch-tenant + rotasi ----------------

class _SConn:
    def __init__(self, dev=True):
        self.dev, self.tulis = dev, []

    def transaction(self):
        return _Acq(self)

    async def execute(self, sql, *a):
        self.tulis.append((" ".join(sql.split()), a))

    async def fetchrow(self, sql, *a):
        if 'FROM "User"' in sql:
            return {"tenantId": "t1", "name": "N", "role": "FREE"}
        if 'FROM "Tenant"' in sql:
            return {"id": "t2"}
        if "FROM user_devices" in sql:
            assert "FOR UPDATE" in sql and a == (DEV, UID)
            return {"refresh_token_hash": "lama"} if self.dev else None
        raise AssertionError(sql[:60])


@pytest.fixture
def sw(monkeypatch):
    c = _SConn()

    async def pool():
        return SimpleNamespace(acquire=lambda: _Acq(c))

    async def peran(conn, u, t):
        return "STAFF"

    monkeypatch.setattr(A, "get_pool", pool)
    monkeypatch.setattr(A, "try_resolve_business_role", peran)
    return c


def _sreq(klaim=True):
    u = {"user_id": UID, "email": "e"}
    if klaim:
        u.update(device_id=DEV, device_type="web")
    return SimpleNamespace(state=SimpleNamespace(user=u))


@pytest.mark.asyncio
async def test_ganti_tenant_klaim_perangkat_dan_refresh_diputar(sw):
    r = await A.switch_tenant(_sreq(), A.SwitchTenantRequest(tenant_id="t2"))
    m = jwt.decode(r["access_token"], RAHASIA, algorithms=["HS256"])
    assert (m["device_id"], m["device_type"], m["tenant_id"]) == (DEV, "web", "t2")
    baru = hashlib.sha256(r["refresh_token"].encode()).hexdigest()
    sqls = [s for s, _ in sw.tulis]
    ins = [a for s, a in sw.tulis if s.startswith("INSERT INTO refresh_tokens")][0]
    assert ins == (UID, "t2", baru)
    assert any(s.startswith("UPDATE refresh_tokens SET revoked_at") and a[0] == "lama" for s, a in sw.tulis)
    upd = [a for s, a in sw.tulis if s.startswith("UPDATE user_devices SET refresh_token_hash")][0]
    assert upd == (baru, "t2", DEV, UID)
    assert "." not in r["refresh_token"]  # opak (format auth_service), bukan JWT tak tersimpan
    assert sqls.index([s for s in sqls if s.startswith("INSERT INTO refresh_tokens")][0]) < sqls.index(
        [s for s in sqls if s.startswith("UPDATE user_devices")][0])


@pytest.mark.asyncio
async def test_ganti_tenant_tanpa_klaim_atau_perangkat_401(sw):
    with pytest.raises(HTTPException) as e:
        await A.switch_tenant(_sreq(klaim=False), A.SwitchTenantRequest(tenant_id="t2"))
    assert e.value.status_code == 401
    sw.dev = False
    with pytest.raises(HTTPException) as e:
        await A.switch_tenant(_sreq(), A.SwitchTenantRequest(tenant_id="t2"))
    assert e.value.status_code == 401 and not any(s.startswith("INSERT") for s, _ in sw.tulis)


# ---------------- login tenant-berubah + /register ----------------

def test_login_tenant_berubah_hanya_akses_ditandatangani_ulang():
    s = " ".join(inspect.getsource(A.login_user).split())
    i = s.index("if resolved_tenant_id != raw_tenant_id:")
    blok = s[i:i + 2500]
    assert '"device_id": device_id' in blok and "jwt_secret_wajib()" in blok
    assert 'result["refresh_token"] = jwt.encode' not in s and "rp = {" not in blok
    assert "UPDATE refresh_tokens SET tenant_id = $1 WHERE token_hash = $2 AND user_id = $3" in blok


def test_register_lama_diparkir():
    from app.services.fitur_parkir import fitur_belum_tersedia
    rute = [r for r in A.router.routes if getattr(r, "path", "").endswith("/register") and "POST" in r.methods][0]
    # auth.py mengimpor lewat jalur absolut backend.api_gateway... -> objek modul BERBEDA dari app.services...
    assert any(getattr(d.call, "__name__", "") == fitur_belum_tersedia.__name__
               and d.call.__module__.endswith("services.fitur_parkir") for d in rute.dependant.dependencies)


def test_token_setup_signup_memakai_rahasia_wajib(monkeypatch):
    from app.routers import signup as SU
    tok = SU.create_setup_token("a@b.c", "reg-1")
    assert jwt.decode(tok, RAHASIA, algorithms=["HS256"])["registration_id"] == "reg-1"
    monkeypatch.delenv("JWT_SECRET", raising=False)
    with pytest.raises(RuntimeError):
        SU.create_setup_token("a@b.c", "reg-1")
