"""Undangan tak lagi bisa MENIMPA sandi akun yang sudah ada (26 Sep 2026, audit WRITE_EXEMPT).

Rantai serangan (terukur di kode, 0 kejadian di data): daftar sendiri -> OWNER
tenant sendiri -> POST /api/team-members/invite {email: korban} (respons memuat
invite_link) -> POST /api/invite/{token}/accept mode B {name, password,
password_confirm} -> dulu `UPDATE "User" SET "passwordHash"` milik korban ->
masuk sebagai korban ke semua tenantnya.

Kini: mode B atas email yang sudah punya akun -> 409 ACCOUNT_EXISTS, NOL tulis
ke "User" / keanggotaan / undangan. Mode A (email + sandi lama) dan mode B untuk
email baru tetap jalan.
"""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routers import invite_public as IP

TOKEN = "tok-uji"
TENANT = "tenant-penyerang"
KORBAN = "korban@contoh.id"
HASH_LAMA = IP._hash_password("SandiAsliKorban1")


class _Tx:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class _Conn:
    """DB tiruan ber-KEADAAN: UPDATE "User" benar-benar mengubah hash di sini."""

    def __init__(self, email_ada: bool):
        self.users = {}
        if email_ada:
            self.users[KORBAN] = {"id": "u-korban", "passwordHash": HASH_LAMA, "isVerified": True}
        self.tulis = []

    def transaction(self):
        return _Tx()

    async def fetchrow(self, sql, *a):
        if "FROM team_invitations" in sql:
            from datetime import datetime, timedelta, timezone
            return {"id": "inv-1", "email": KORBAN, "name": None, "status": "pending",
                    "expires_at": datetime.now(timezone.utc) + timedelta(days=1),
                    "tenant_id": TENANT, "role_id": "r-1", "module_overrides": None,
                    "invited_by": "u-penyerang", "role_code": "STAFF", "role_name": "Staf",
                    "verify_code_hash": None, "verify_code_expires_at": None, "verify_attempts": 0}
        if 'FROM "User"' in sql:
            u = self.users.get(a[0])
            if not u:
                return None
            return {"id": u["id"], "passwordHash": u["passwordHash"]}
        raise AssertionError("fetchrow tak terduga: " + sql[:60])

    async def fetchval(self, sql, *a):
        if 'FROM "User"' in sql:
            return 1 if a[0].lower() in self.users else None
        return None  # belum anggota

    async def execute(self, sql, *a):
        s = " ".join(sql.split())
        if s.startswith("SET") or "set_config" in s:
            return
        self.tulis.append(s)
        if s.startswith('UPDATE "User" SET "passwordHash"'):
            for u in self.users.values():
                if u["id"] == a[2]:
                    u["passwordHash"] = a[0]
                    u["isVerified"] = True
        if s.startswith('INSERT INTO "User"'):
            self.users[a[1]] = {"id": a[0], "passwordHash": a[3], "isVerified": True}


class _Acq:
    def __init__(self, c):
        self.c = c

    async def __aenter__(self):
        return self.c

    async def __aexit__(self, *a):
        return False


class _Pool:
    def __init__(self, c):
        self.c = c

    def acquire(self):
        return _Acq(self.c)


def _klien(monkeypatch, conn):
    async def pool():
        return _Pool(conn)

    async def jwt(**k):
        return {"access_token": "a", "refresh_token": "r", "device_id": "d"}

    monkeypatch.setattr(IP, "get_db_pool", pool)
    monkeypatch.setattr(IP, "_generate_jwt_tokens", jwt)
    app = FastAPI()
    app.include_router(IP.router)
    return TestClient(app)


MODE_B = {"name": "Penyerang", "password": "SandiPenyerang9", "password_confirm": "SandiPenyerang9"}


def test_mode_b_atas_akun_lama_409_hash_utuh(monkeypatch):
    conn = _Conn(email_ada=True)
    sebelum = dict(conn.users[KORBAN])
    r = _klien(monkeypatch, conn).post(f"/api/invite/{TOKEN}/accept", json=MODE_B)
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["code"] == "ACCOUNT_EXISTS"
    assert conn.users[KORBAN]["passwordHash"] == sebelum["passwordHash"]  # byte-identik
    assert conn.users[KORBAN]["isVerified"] == sebelum["isVerified"]
    assert conn.tulis == [], conn.tulis  # nol tulis: User, keanggotaan, undangan, audit


def test_respons_409_tak_membocorkan_data_akun(monkeypatch):
    conn = _Conn(email_ada=True)
    r = _klien(monkeypatch, conn).post(f"/api/invite/{TOKEN}/accept", json=MODE_B)
    teks = r.text
    for bocor in ("u-korban", "passwordHash", HASH_LAMA, "access_token"):
        assert bocor not in teks


def test_mode_a_akun_lama_tetap_bisa_bergabung(monkeypatch):
    conn = _Conn(email_ada=True)
    r = _klien(monkeypatch, conn).post(
        f"/api/invite/{TOKEN}/accept", json={"email": KORBAN, "password": "SandiAsliKorban1"}
    )
    assert r.status_code == 200, r.text
    assert conn.users[KORBAN]["passwordHash"] == HASH_LAMA
    assert any(s.startswith("INSERT INTO user_tenant_roles") for s in conn.tulis)


def test_mode_a_sandi_salah_ditolak(monkeypatch):
    conn = _Conn(email_ada=True)
    r = _klien(monkeypatch, conn).post(
        f"/api/invite/{TOKEN}/accept", json={"email": KORBAN, "password": "tebakan123"}
    )
    assert r.status_code == 401
    assert conn.tulis == []


def test_mode_b_email_baru_tanpa_kode_ditolak(monkeypatch):
    # V311: akun baru dari undangan wajib kode ke email undangan (alur lengkap:
    # tests/unit/test_undangan_kode_verifikasi.py).
    conn = _Conn(email_ada=False)
    r = _klien(monkeypatch, conn).post(f"/api/invite/{TOKEN}/accept", json=MODE_B)
    assert r.status_code == 400, r.text
    assert r.json()["detail"]["code"] == "CODE_REQUIRED"
    assert KORBAN not in conn.users
    assert conn.tulis == []
