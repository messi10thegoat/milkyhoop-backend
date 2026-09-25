"""Sisa audit WRITE_EXEMPT (26 Sep 2026): remote-scan pemilik sesi + decline undangan kedaluwarsa.

- POST /api/devices/remote-scan/cancel: dulu TANPA cek apa pun -> login mana pun
  (lintas tenant) yang tahu scan_id membatalkan scan orang lain.
- POST /remote-scan/result: cek tenant saja dan meng-POP sesi SEBELUM memvalidasi
  -> penolakan pun menghabiskan sesi korban.
- POST /api/invite/{token}/decline: undangan kedaluwarsa masih bisa "ditolak".
"""
import copy
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.services.websocket_hub import WebSocketHub
from app.routers import invite_public as IP

SCAN = "scan-1"


def _hub():
    h = WebSocketHub.__new__(WebSocketHub)
    h.remote_scan_sessions = {SCAN: {"tenant_id": "ta", "user_id": "u1", "mobile_device_id": "m1",
                                     "desktop_device_id": "d1", "desktop_tab_id": "t1"}}
    h.device_connections = {}
    import asyncio
    h._lock = asyncio.Lock()
    return h


@pytest.mark.parametrize("tenant,user", [("tb", "u1"), ("ta", "u2"), ("tb", "u9")])
def test_result_bukan_pemilik_ditolak_sesi_utuh(tenant, user):
    h = _hub()
    assert h.pop_and_validate_session(SCAN, tenant, user) is None
    assert SCAN in h.remote_scan_sessions  # TIDAK dihabiskan


def test_result_pemilik_mengambil_sesi():
    h = _hub()
    assert h.pop_and_validate_session(SCAN, "ta", "u1")["mobile_device_id"] == "m1"
    assert SCAN not in h.remote_scan_sessions


@pytest.mark.asyncio
@pytest.mark.parametrize("tenant,user", [("tb", "u1"), ("ta", "u2"), (None, None)])
async def test_cancel_bukan_pemilik_ditolak_sesi_utuh(tenant, user):
    h = _hub()
    assert await h.cancel_remote_scan(SCAN, tenant, user) is False
    assert SCAN in h.remote_scan_sessions


@pytest.mark.asyncio
async def test_cancel_pemilik_menghapus_sesi():
    h = _hub()
    await h.cancel_remote_scan(SCAN, "ta", "u1")  # mobile tak terhubung -> False, tapi sesi dibuang
    assert SCAN not in h.remote_scan_sessions


def test_sesi_lama_tanpa_user_tetap_dilayani_se_tenant():
    h = _hub()
    h.remote_scan_sessions[SCAN]["user_id"] = None
    assert h.pop_and_validate_session(SCAN, "tb", "u1") is None
    assert h.pop_and_validate_session(SCAN, "ta", "u2") is not None


# ---------- decline kedaluwarsa ----------

class _Tx:
    def __init__(self, db):
        self.db = db

    async def __aenter__(self):
        self.snap = copy.deepcopy(self.db)

    async def __aexit__(self, et, *a):
        if et is not None:
            self.db.clear(); self.db.update(self.snap)
        return False


class _Conn:
    def __init__(self, db):
        self.db = db

    def transaction(self):
        return _Tx(self.db)

    async def fetchrow(self, sql, *a):
        return dict(self.db["inv"])

    async def execute(self, sql, *a):
        if "status = 'expired'" in sql:
            self.db["inv"]["status"] = "expired"
        elif "status = 'declined'" in sql:
            self.db["inv"]["status"] = "declined"


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


def _klien(monkeypatch, lewat):
    db = {"inv": {"id": "i1", "email": "a@b.c", "tenant_id": "t", "invited_by": "u", "status": "pending",
                  "expires_at": datetime.now(timezone.utc) + (timedelta(days=-1) if lewat else timedelta(days=1))}}

    async def pool():
        return _Pool(db)

    monkeypatch.setattr(IP, "get_db_pool", pool)
    app = FastAPI()
    app.include_router(IP.router)
    return TestClient(app), db


def test_decline_kedaluwarsa_400_dan_tercatat_expired(monkeypatch):
    k, db = _klien(monkeypatch, lewat=True)
    r = k.post("/api/invite/tok/decline")
    assert r.status_code == 400 and "kedaluwarsa" in r.text
    assert db["inv"]["status"] == "expired"  # ter-commit, bukan 'declined'


def test_decline_berlaku_tetap_jalan(monkeypatch):
    k, db = _klien(monkeypatch, lewat=False)
    r = k.post("/api/invite/tok/decline")
    assert r.status_code == 200
    assert db["inv"]["status"] == "declined"


def test_router_meneruskan_pemilik_ke_hub():
    """Tanpa user_id dari router, cek pemilik di hub tak pernah aktif (sesi user_id None)."""
    from pathlib import Path
    src = (Path(__file__).parents[2] / "app/routers/device.py").read_text(encoding="utf-8")
    s = " ".join(src.split())
    assert 'tenant_id=user["tenant_id"], user_id=user["user_id"], )' in s
    assert 'pop_and_validate_session(body.scan_id, user_tenant, user["user_id"])' in s
    assert 'cancel_remote_scan( body.scan_id, _user["tenant_id"], _user["user_id"] )' in s
