"""Audit WRITE_EXEMPT chat (26 Sep 2026) — pemilik kartu aksi, pemilik sesi, keanggotaan.

Terukur di kode (dikonfirmasi ulang dari laporan agen):
- confirm/cancel/edit kartu aksi menyaring `tenant_id` saja walau
  pending_actions.user_id terisi (33/33) -> rekan se-tenant yang tahu id bisa
  membatalkan / MENGUBAH kartu orang lain (lalu pemiliknya mengonfirmasi dengan
  izinnya sendiri = confused deputy).
- /cancel tanpa session_id meng-IDLE-kan sesi AWAITING se-TENANT.
- get_or_create_session menerima session_id siapa pun se-tenant (riwayat orang
  lain jadi konteks LLM pemanggil); chat_sessions.user_id terisi 82/82.
- seluruh rute chat exempt -> tak ada cek anggota aktif.
- POST /{tenant_id}/chat (publik) membalas 500 berisi teks galat internal.
"""
import os
import re
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("OPENAI_API_KEY", "sk-boneka-unit-test-tanpa-jaringan")

import pytest  # noqa: E402
from fastapi import FastAPI, HTTPException  # noqa: E402
from starlette.middleware.base import BaseHTTPMiddleware  # noqa: E402
from starlette.testclient import TestClient  # noqa: E402

from app.routers import unified_chat as UC  # noqa: E402
from app.services import policy_engine_client as PEC  # noqa: E402
from app.services.unified_agent.session_manager import SessionManager  # noqa: E402

APP = Path(__file__).parents[2] / "app"
SAYA = "00000000-0000-0000-0000-00000000000a"
LAIN = "00000000-0000-0000-0000-00000000000b"
KARTU = "00000000-0000-0000-0000-0000000000c1"


def _ctx(role_id="r-1"):
    return SimpleNamespace(membership_active=True, business_role_id=role_id, business_role_code="STAFF")


class _Eng:
    def __init__(self, ctx):
        self.ctx = ctx

    async def get_user_context(self, *a, **k):
        return self.ctx


class _Pool:
    """pending_actions tiruan: KARTU milik SAYA."""

    def __init__(self):
        self.sql = []

    async def fetchval(self, sql, *a):
        self.sql.append((sql, a))
        if "FROM pending_actions" in sql and "user_id = $3" in sql:
            return 1 if (a[0] == KARTU and a[2] == SAYA) else None
        return None

    async def execute(self, sql, *a):
        self.sql.append((sql, a))
        return "UPDATE 0"


# ---------- keanggotaan ----------

@pytest.mark.asyncio
async def test_anggota_tanpa_baris_ditolak(monkeypatch):
    monkeypatch.setattr(PEC, "get_policy_engine", lambda: _Eng(_ctx(role_id=None)))
    req = SimpleNamespace(state=SimpleNamespace(user={"user_id": SAYA, "tenant_id": "t", "role": "ADMIN"}))
    with pytest.raises(HTTPException) as e:
        await UC._wajib_anggota_aktif_chat(req)
    assert e.value.status_code == 403
    assert e.value.detail["error_code"] == "MEMBERSHIP_INACTIVE"


@pytest.mark.asyncio
async def test_anggota_aktif_lolos(monkeypatch):
    monkeypatch.setattr(PEC, "get_policy_engine", lambda: _Eng(_ctx()))
    req = SimpleNamespace(state=SimpleNamespace(user={"user_id": SAYA, "tenant_id": "t", "role": "ADMIN"}))
    assert await UC._wajib_anggota_aktif_chat(req) is None


def test_pagar_di_level_router_semua_rute_chat():
    assert any(d.dependency is UC._wajib_anggota_aktif_chat for d in UC.router.dependencies)
    for r in UC.router.routes:
        if hasattr(r, "dependant"):
            assert any(d.call is UC._wajib_anggota_aktif_chat for d in r.dependant.dependencies), r.path


# ---------- pemilik kartu aksi ----------

@pytest.mark.asyncio
async def test_pemilik_kartu():
    p = _Pool()
    assert await UC._pending_milik_pemanggil(p, KARTU, "t", SAYA) is True
    assert await UC._pending_milik_pemanggil(p, KARTU, "t", LAIN) is False
    assert await UC._pending_milik_pemanggil(p, "bukan-uuid", "t", SAYA) is False


class _SetUser(BaseHTTPMiddleware):
    async def dispatch(self, req, nxt):
        req.state.user = {"user_id": req.headers["x-u"], "tenant_id": "t", "role": "ADMIN"}
        return await nxt(req)


@pytest.fixture
def klien(monkeypatch):
    pool = _Pool()

    async def gp():
        return pool

    monkeypatch.setattr(PEC, "get_policy_engine", lambda: _Eng(_ctx()))
    monkeypatch.setattr(UC, "get_session_db_pool", gp)
    app = FastAPI()
    app.include_router(UC.router, prefix="/api/v3/chat")
    app.add_middleware(_SetUser)
    return TestClient(app, raise_server_exceptions=False), pool


@pytest.mark.parametrize("jalur,badan", [
    ("/confirm", {"conversation_id": "c", "pending_action_id": KARTU}),
    ("/action/edit", {"conversation_id": "c", "pending_action_id": KARTU, "text": "ubah jumlah jadi 1"}),
])
def test_kartu_orang_lain_404_sebelum_logika(klien, jalur, badan):
    k, pool = klien
    r = k.post("/api/v3/chat" + jalur, json=badan, headers={"x-u": LAIN})
    assert r.status_code == 404, r.text
    # satu-satunya kueri = cek pemilik; tak ada baca/tulis kartu lain
    assert all("user_id = $3" in s for s, _ in pool.sql), pool.sql


def test_batal_hanya_kartu_milik_sendiri_dan_sesi_sendiri():
    src = (APP / "routers/unified_chat.py").read_text(encoding="utf-8")
    assert "WHERE id = $1::uuid AND tenant_id = $2 AND user_id = $3 AND status = 'PENDING'" in src
    i = src.index("SET fsm_state = 'IDLE'")
    potong = " ".join(src[i:i + 500].split())
    assert "session_id IN (SELECT id FROM chat_sessions WHERE tenant_id = $1 AND user_id::text = $2)" in potong


# ---------- pemilik sesi ----------

class _DB:
    def __init__(self):
        self.q = []

    async def fetchrow(self, sql, *a):
        self.q.append((sql, a))
        return None

    async def fetchval(self, sql, *a):
        return None

    async def execute(self, sql, *a):
        return "INSERT 0 1"


@pytest.mark.asyncio
@pytest.mark.parametrize("uid,harap", [(SAYA, SAYA), (None, None)])
async def test_sesi_disaring_pemilik_bila_user_diketahui(uid, harap):
    db = _DB()
    sm = SessionManager(db_pool=db, tenant_id="t", user_id=uid)
    try:
        await sm.get_or_create_session("00000000-0000-0000-0000-0000000000e1")
    except Exception:
        pass  # jalur pembuatan sesi baru di luar lingkup tes ini
    sql, args = db.q[0]
    assert "($4::text IS NULL OR user_id::text = $4)" in " ".join(sql.split())
    assert args[3] == harap


# ---------- chat publik ----------

def test_chat_publik_tak_membocorkan_teks_galat():
    src = (APP / "routers/public_chat.py").read_text(encoding="utf-8")
    assert not re.search(r"detail=f\"Internal server error: \{str\(e\)\}\"", src)


@pytest.mark.parametrize("jalur,badan", [
    ("/confirm", {"conversation_id": "c", "pending_action_id": KARTU}),
    ("/action/edit", {"conversation_id": "c", "pending_action_id": KARTU, "text": "ubah jumlah jadi 1"}),
])
def test_pemilik_kartu_melewati_cek(klien, jalur, badan):
    """Kontrol positif: cek yang menolak SEMUA orang juga lolos tes 404 di atas."""
    k, pool = klien
    r = k.post("/api/v3/chat" + jalur, json=badan, headers={"x-u": SAYA})
    assert "Aksi tidak ditemukan" not in r.text, r.text  # lewat cek pemilik
    assert pool.sql and pool.sql[0][1][2] == SAYA
