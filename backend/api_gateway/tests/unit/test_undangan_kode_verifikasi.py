"""V311 (26 Sep 2026) — akun BARU dari undangan wajib kode 6 digit ke email UNDANGAN.

Dulu pemegang token (pengundang selalu menerima invite_link) membuat akun untuk
email orang lain dengan sandi pilihannya, isVerified=true. Kini:
- POST /api/invite/{token}/request-code: kirim kode ke email undangan; kode tak
  pernah di respons; 1x/60 dtk dan maks 5x/jam per token; email ber-akun -> 409.
- accept mode B: tanpa/ salah/ kedaluwarsa kode -> 400, NOL akun; 5 salah ->
  kode terkunci; hitungan salah TETAP tersimpan walau permintaan ditolak.
DB tiruan ber-keadaan DENGAN rollback transaksi (raise di dalam transaksi
membatalkan tulisannya, seperti Postgres).
"""
import copy
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routers import invite_public as IP
from app.services import email_service as ES

TOKEN = "tok-kode"
EMAIL = "orang.baru@contoh.id"
MODE_B = {"name": "Baru", "password": "SandiBaru123", "password_confirm": "SandiBaru123"}


class _DB:
    def __init__(self, email_ada=False):
        self.s = {
            "inv": {"id": "inv-1", "email": EMAIL, "name": None, "status": "pending",
                    "expires_at": datetime.now(timezone.utc) + timedelta(days=1),
                    "tenant_id": "t-1", "role_id": "r-1", "module_overrides": None,
                    "invited_by": "u-pengundang", "role_code": "STAFF", "role_name": "Staf",
                    "verify_code_hash": None, "verify_code_expires_at": None, "verify_attempts": 0,
                    "verify_sent_at": None, "verify_sent_window_at": None, "verify_sent_count": 0},
            "users": {EMAIL: {"id": "u-lama"}} if email_ada else {},
            "anggota": [],
        }


class _Tx:
    def __init__(self, db):
        self.db = db

    async def __aenter__(self):
        self.snap = copy.deepcopy(self.db.s)

    async def __aexit__(self, et, *a):
        if et is not None:
            self.db.s = self.snap  # ROLLBACK
        return False


class _Conn:
    def __init__(self, db):
        self.db = db

    def transaction(self):
        return _Tx(self.db)

    async def fetchrow(self, sql, *a):
        if "FROM team_invitations" in sql:
            return dict(self.db.s["inv"]) if a[0] == TOKEN else None
        if 'FROM "User"' in sql:
            u = self.db.s["users"].get(a[0])
            return {"id": u["id"], "passwordHash": "x"} if u else None
        raise AssertionError(sql[:60])

    async def fetchval(self, sql, *a):
        if 'FROM "User"' in sql:
            return 1 if a[0].lower() in self.db.s["users"] else None
        return None

    async def execute(self, sql, *a):
        s = " ".join(sql.split())
        inv = self.db.s["inv"]
        if s.startswith("SET"):
            return
        if "SET verify_code_hash = $2" in s:
            (inv["verify_code_hash"], inv["verify_code_expires_at"], inv["verify_sent_at"],
             inv["verify_sent_window_at"], inv["verify_sent_count"]) = a[1], a[2], a[3], a[4], a[5]
            inv["verify_attempts"] = 0
        elif "verify_attempts = verify_attempts + 1" in s:
            inv["verify_attempts"] += 1
        elif s.startswith('INSERT INTO "User"'):
            self.db.s["users"][a[1]] = {"id": a[0]}
        elif s.startswith("INSERT INTO user_tenant_roles"):
            self.db.s["anggota"].append(a[0])
        elif "SET status = 'accepted'" in s:
            inv["status"] = "accepted"
            if "verify_code_hash = NULL" in s:
                inv["verify_code_hash"] = None


class _Acq:
    def __init__(self, db):
        self.db = db

    async def __aenter__(self):
        return _Conn(self.db)

    async def __aexit__(self, *a):
        return False


class _Pool:
    def __init__(self, db):
        self.db = db

    def acquire(self):
        return _Acq(self.db)


@pytest.fixture
def uji(monkeypatch):
    db = _DB()
    terkirim = []

    async def pool():
        return _Pool(db)

    async def kirim(email, kode):
        terkirim.append((email, kode))
        return True

    async def jwt(**k):
        return {"access_token": "a", "refresh_token": "r", "device_id": "d"}

    monkeypatch.setattr(IP, "get_db_pool", pool)
    monkeypatch.setattr(IP, "_generate_jwt_tokens", jwt)
    monkeypatch.setattr(ES, "send_invite_code_email", kirim)
    app = FastAPI()
    app.include_router(IP.router)
    return TestClient(app), db, terkirim


def _minta(k):
    return k.post(f"/api/invite/{TOKEN}/request-code")


def _terima(k, kode=None):
    badan = dict(MODE_B)
    if kode is not None:
        badan["code"] = kode
    return k.post(f"/api/invite/{TOKEN}/accept", json=badan)


def test_kode_ke_email_undangan_tak_pernah_di_respons(uji):
    k, db, terkirim = uji
    r = _minta(k)
    assert r.status_code == 200, r.text
    assert terkirim and terkirim[0][0] == EMAIL
    kode = terkirim[0][1]
    assert len(kode) == 6 and kode.isdigit()
    assert kode not in r.text
    assert db.s["inv"]["verify_code_hash"] and kode not in db.s["inv"]["verify_code_hash"]


def test_alur_benar_membuat_akun_dan_kode_habis(uji):
    k, db, terkirim = uji
    _minta(k)
    r = _terima(k, terkirim[0][1])
    assert r.status_code == 200, r.text
    assert EMAIL in db.s["users"] and db.s["anggota"]
    assert db.s["inv"]["verify_code_hash"] is None and db.s["inv"]["status"] == "accepted"


def test_tanpa_minta_kode_400_nol_akun(uji):
    k, db, _ = uji
    r = _terima(k, "123456")
    assert r.status_code == 400 and r.json()["detail"]["code"] == "CODE_REQUIRED"
    assert db.s["users"] == {}


def test_kode_salah_400_hitungan_tersimpan_walau_ditolak(uji):
    k, db, terkirim = uji
    _minta(k)
    benar = terkirim[0][1]
    salah = "000000" if benar != "000000" else "111111"
    r = _terima(k, salah)
    assert r.status_code == 400 and r.json()["detail"]["code"] == "CODE_INVALID"
    assert db.s["inv"]["verify_attempts"] == 1  # BUKAN dibatalkan rollback
    assert db.s["users"] == {}


def test_lima_salah_kode_terkunci_bahkan_untuk_kode_benar(uji):
    k, db, terkirim = uji
    _minta(k)
    benar = terkirim[0][1]
    salah = "000000" if benar != "000000" else "111111"
    for _ in range(5):
        _terima(k, salah)
    r = _terima(k, benar)
    assert r.status_code == 400 and r.json()["detail"]["code"] == "CODE_LOCKED"
    assert db.s["users"] == {}


def test_kode_kedaluwarsa(uji):
    k, db, terkirim = uji
    _minta(k)
    db.s["inv"]["verify_code_expires_at"] = datetime.now(timezone.utc) - timedelta(seconds=1)
    r = _terima(k, terkirim[0][1])
    assert r.status_code == 400 and r.json()["detail"]["code"] == "CODE_EXPIRED"
    assert db.s["users"] == {}


def test_jeda_60_detik(uji):
    k, db, terkirim = uji
    assert _minta(k).status_code == 200
    r = _minta(k)
    assert r.status_code == 429 and r.json()["detail"]["code"] == "CODE_TOO_SOON"
    assert len(terkirim) == 1


def test_maks_5_per_jam_per_token(uji):
    k, db, terkirim = uji
    for i in range(5):
        db.s["inv"]["verify_sent_at"] = None  # lewati jeda 60 dtk, jendela jam tetap
        assert _minta(k).status_code == 200, i
    db.s["inv"]["verify_sent_at"] = None
    r = _minta(k)
    assert r.status_code == 429 and r.json()["detail"]["code"] == "CODE_TOO_MANY"
    assert len(terkirim) == 5
    # jendela lewat -> boleh lagi
    db.s["inv"]["verify_sent_window_at"] = datetime.now(timezone.utc) - timedelta(hours=1, seconds=1)
    assert _minta(k).status_code == 200


def test_email_ber_akun_request_code_409_tanpa_kirim(uji):
    k, db, terkirim = uji
    db.s["users"][EMAIL] = {"id": "u-lama"}
    r = _minta(k)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "ACCOUNT_EXISTS"
    assert terkirim == []


def test_gagal_kirim_email_jatah_tak_terpakai(uji, monkeypatch):
    k, db, _ = uji

    async def gagal(email, kode):
        raise ES.EmailDeliveryUnavailable("uji")

    monkeypatch.setattr(ES, "send_invite_code_email", gagal)
    r = _minta(k)
    assert r.status_code == 503
    assert db.s["inv"]["verify_sent_count"] == 0 and db.s["inv"]["verify_code_hash"] is None


def test_rute_request_code_publik_per_rute_bukan_prefix():
    from app.middleware.auth_middleware import AuthMiddleware
    mw = AuthMiddleware.__new__(AuthMiddleware)
    assert mw._is_public_invite(f"/api/invite/{TOKEN}/request-code", "POST")
    assert not mw._is_public_invite(f"/api/invite/{TOKEN}/request-code", "GET")
    assert not mw._is_public_invite(f"/api/invite/{TOKEN}/lain", "POST")
