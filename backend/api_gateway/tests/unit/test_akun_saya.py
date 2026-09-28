"""D2 sidebar (28 Sep 2026): GET /api/tenants/mine & GET /api/me — adaptor atas keanggotaan & profil yang sudah ada.
Pagar utama: tenants/mine TAK boleh membocorkan usaha user lain; tenant aktif = JWT (bukan header)."""
import re

import pytest

from app.middleware import permission_middleware as PM
from app.routers import user as U
from app.services import akun_saya as A

SEMUA_TENANT = [   # isi "Tenant" di DB palsu: termasuk usaha orang lain & usaha ditangguhkan
    {"id": "kaos", "display_name": "Kaos Biru Konveksi", "alias": None, "logo_url": "k.png", "suspended": False},
    {"id": "grap", "display_name": "Grapgrap Clothing", "alias": None, "logo_url": None, "suspended": False},
    {"id": "orang-lain", "display_name": "Usaha Orang Lain", "alias": None, "logo_url": None, "suspended": False},
    {"id": "beku", "display_name": "Ditangguhkan", "alias": None, "logo_url": None, "suspended": True},
]


class _Conn:
    def __init__(self):
        self.sql = []

    async def fetch(self, sql, *a):
        self.sql.append((sql, a))
        if 'FROM "Tenant"' in sql:
            rows = SEMUA_TENANT
            if "id = ANY($1::text[])" in sql:
                rows = [r for r in rows if r["id"] in a[0]]
            if "suspended_at IS NULL" in sql:
                rows = [r for r in rows if not r["suspended"]]
            return [dict(r) for r in rows]
        if "FROM roles" in sql:
            return [{"tenant_id": "__SYSTEM__", "code": "OWNER", "name": "Owner"},
                    {"tenant_id": "__SYSTEM__", "code": "ACCOUNTANT", "name": "Accountant"},
                    {"tenant_id": "grap", "code": "ACCOUNTANT", "name": "Akuntan Grapgrap"}]
        raise AssertionError(sql)

    async def fetchrow(self, sql, *a):
        self.sql.append((sql, a))
        if 'FROM "User"' in sql:
            return {"name": "Anton", "email": "a@x.id"}
        if "user_profiles" in sql:
            return {"display_name": None}
        if 'FROM "Tenant"' in sql:
            return {"id": a[0], "display_name": "Kaos Biru Konveksi", "alias": None, "plan_tier": "BASE"}
        raise AssertionError(sql)


@pytest.fixture
def keanggotaan(monkeypatch):
    from app.services import role_resolution as RR

    async def daftar(conn, user_id):
        return {"kaos": "OWNER", "grap": "ACCOUNTANT", "beku": "OWNER"} if user_id == "u1" else {"orang-lain": "OWNER"}

    async def peran(conn, user_id, tenant_id):
        return {"kaos": "OWNER"}.get(tenant_id)
    monkeypatch.setattr(RR, "list_active_tenant_roles", daftar)
    monkeypatch.setattr(RR, "try_resolve_business_role", peran)


@pytest.mark.parametrize("nama,harap", [("Grapgrap Clothing", "GC"), ("kaos", "K"), ("Kaos Biru Konveksi", "KB"),
                                        ("", "?"), (None, "?"), ("toko-manado", "TM")])
def test_inisial(nama, harap):
    assert A.inisial(nama) == harap and len(A.inisial(nama)) <= 2


@pytest.mark.asyncio
async def test_tenants_mine_hanya_keanggotaan_sendiri_dan_bentuk_kontrak(keanggotaan):
    r = await A.usaha_saya(_Conn(), "u1", "kaos")
    ids = [t["tenant_id"] for t in r["tenants"]]
    assert ids == ["kaos", "grap"]   # aktif dulu; 'orang-lain' TAK bocor; 'beku' (ditangguhkan) tak tampil
    assert r["can_create_tenant"] is False
    for t in r["tenants"]:
        assert {"tenant_id", "name", "initial", "role", "is_active"} <= set(t) and len(t["initial"]) <= 2
    assert r["tenants"][0] == {**r["tenants"][0], "is_active": True, "role": "Owner", "initial": "KB"}
    assert r["tenants"][1]["role"] == "Akuntan Grapgrap" and r["tenants"][1]["is_active"] is False  # peran tenant menang


@pytest.mark.asyncio
async def test_user_lain_hanya_melihat_miliknya(keanggotaan):
    r = await A.usaha_saya(_Conn(), "u2", "kaos")
    assert [t["tenant_id"] for t in r["tenants"]] == ["orang-lain"]
    assert not any(t["is_active"] for t in r["tenants"])   # JWT tenant 'kaos' bukan miliknya -> tak ditandai aktif


@pytest.mark.asyncio
async def test_tanpa_keanggotaan_kosong(monkeypatch):
    from app.services import role_resolution as RR

    async def kosong(conn, user_id):
        return {}
    monkeypatch.setattr(RR, "list_active_tenant_roles", kosong)
    c = _Conn()
    assert await A.usaha_saya(c, "u3", "kaos") == {"tenants": [], "can_create_tenant": False}
    assert not c.sql   # tak ada kueri Tenant sama sekali


@pytest.mark.asyncio
async def test_me_nama_email_peran_paket(keanggotaan):
    r = await A.akun_saya(_Conn(), "u1", "jwt@x.id", "kaos")
    assert r["name"] == "Anton" and r["email"] == "a@x.id" and r["initials"] == "A"
    assert r["role"] == "Owner" and r["role_code"] == "OWNER" and r["plan_label"] == "Paket Dasar"
    assert r["tenant"] == {"tenant_id": "kaos", "name": "Kaos Biru Konveksi"}


class _Req:
    def __init__(self, user, headers=None):
        class S:
            pass
        self.state = S()
        self.state.user = user
        self.headers = headers or {}


@pytest.mark.asyncio
async def test_rute_memakai_tenant_jwt_bukan_header(monkeypatch):
    dipakai = {}

    async def usaha(conn, user_id, tenant_aktif):
        dipakai.update(user_id=user_id, tenant=tenant_aktif)
        return {"tenants": [], "can_create_tenant": False}

    class _C:
        async def close(self):
            pass

    async def koneksi():
        return _C()
    monkeypatch.setattr(A, "usaha_saya", usaha)
    monkeypatch.setattr(U, "get_db_connection", koneksi)
    r = await U.get_tenants_mine(_Req({"user_id": "u1", "tenant_id": "kaos"}, {"X-Tenant-ID": "orang-lain"}))
    assert r["success"] is True and dipakai == {"user_id": "u1", "tenant": "kaos"}


@pytest.mark.asyncio
async def test_rute_tanpa_user_401():
    from fastapi import HTTPException
    for fn in (U.get_tenants_mine, U.get_me):
        with pytest.raises(HTTPException) as e:
            await fn(_Req({}))
        assert e.value.status_code == 401


def test_allowlist_baca_ter_anchor():
    pola = PM.READ_DEFAULT_OPEN_ALLOWLIST
    assert r"^/api/me$" in pola and r"^/api/tenants/mine$" in pola
    cocok = lambda p: any(re.match(x, p) for x in pola)
    assert cocok("/api/me") and cocok("/api/tenants/mine")
    for jalur in ("/api/me/x", "/api/members", "/api/tenants/mine2", "/api/tenants", "/api/tenants/kaos"):
        assert not cocok(jalur), jalur
