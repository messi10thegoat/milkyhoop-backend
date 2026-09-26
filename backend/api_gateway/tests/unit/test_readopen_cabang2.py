"""Audit READ_OPEN cabang 2 (26 Sep 2026): R2 intake GET, R4 chat, R5 rute lama/stub.

R2 GET /api/document-intake/{document,review,batch,batch/progress,stats} dulu
tenant saja -> ocr_result/draft_plan dokumen modul mana pun terbaca (0 baris
prod, laten). Kini anggota aktif + saring per modul doc_type (OWNER semua).
R4 /api/v3/chat/usage dulu se-tenant (dan user_id dibaca dari kunci "id" yang
tak ada); /status/{id} tanpa pemilik; router chat_history/chat_usage/userguide
tanpa pagar anggota aktif.
R5 /chat/history (id dari QUERY), /chat/, /chat/test, /api/session/list,
/api/devices/stats -> 409 diparkir.
"""
import os
from types import SimpleNamespace

os.environ.setdefault("OPENAI_API_KEY", "sk-boneka-unit-test-tanpa-jaringan")

import pytest  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from starlette.middleware.base import BaseHTTPMiddleware  # noqa: E402
from starlette.testclient import TestClient  # noqa: E402

from app.routers import document_intake as DI  # noqa: E402
from app.routers import chat_usage as CU  # noqa: E402
from app.routers import chat_history as CH  # noqa: E402
from app.routers import userguide_doc as UG  # noqa: E402
from app.routers import unified_chat as UC  # noqa: E402
from app.services import policy_engine_client as PEC  # noqa: E402
from app.services.role_resolution import require_active_membership  # noqa: E402

USER = "00000000-0000-0000-0000-0000000000aa"
NOTA, TAGIHAN = "10000000-0000-0000-0000-000000000001", "10000000-0000-0000-0000-000000000002"
BATCH = "20000000-0000-0000-0000-000000000001"


def _doc(i, dt):
    return {"id": i, "doc_type": dt, "status": "draft_ready", "original_filename": dt + ".jpg",
            "tenant_id": "t", "batch_id": BATCH}


class _Eng:
    async def get_user_context(self, user_id, tenant_id, subscription_role):
        peran = {"OWNER": "OWNER", "NONAKTIF": None}.get(subscription_role, "STAFF")
        return SimpleNamespace(membership_active=True, business_role_id=None if peran is None else "r",
                               business_role_code=peran)

    async def can(self, ctx, aksi, modul):
        return aksi == "R" and modul == "receipt"  # staf: nota saja, TANPA tagihan


class _SetUser(BaseHTTPMiddleware):
    async def dispatch(self, req, nxt):
        req.state.user = {"user_id": USER, "tenant_id": "t", "role": req.headers.get("x-peran", "STAFF")}
        return await nxt(req)


@pytest.fixture
def intake(monkeypatch):
    docs = [_doc(NOTA, "receipt"), _doc(TAGIHAN, "bill")]

    class _Svc:
        async def get_document_detail(self, tid, did):
            return next((d for d in docs if d["id"] == str(did)), None)

        async def get_review_queue(self, tid, status_filter=None, limit=50, offset=0):
            return docs[offset:offset + limit], len(docs)

        async def get_batch_status(self, tid, bid):
            return {"id": BATCH}, list(docs)

    async def svc():
        return _Svc()

    monkeypatch.setattr(PEC, "get_policy_engine", lambda: _Eng())
    monkeypatch.setattr(DI, "_get_service", svc)
    monkeypatch.setattr(DI, "_doc_data", lambda d: {"id": d["id"], "doc_type": d["doc_type"]})
    monkeypatch.setattr(DI, "_doc_detail", lambda d: {"id": d["id"], "doc_type": d["doc_type"]})
    monkeypatch.setattr(DI, "_batch_summary", lambda b: {"id": b["id"]})
    app = FastAPI()
    app.include_router(DI.router, prefix="/api/document-intake")
    app.add_middleware(_SetUser)
    return TestClient(app, raise_server_exceptions=False)


def _pakai_model_bebas(monkeypatch):
    for nama in ("DocumentIntakeDetailResponse", "ReviewQueueResponse", "BatchStatusResponse"):
        monkeypatch.setattr(DI, nama, lambda **k: k, raising=False)


def _req(peran="STAFF"):
    return SimpleNamespace(state=SimpleNamespace(user={"user_id": USER, "tenant_id": "t", "role": peran}))


async def _kode(coro):
    from fastapi import HTTPException
    try:
        return 200, await coro
    except HTTPException as e:
        return e.status_code, None


@pytest.mark.asyncio
async def test_detail_tagihan_tanpa_izin_404_nota_200(intake, monkeypatch):
    from uuid import UUID
    _pakai_model_bebas(monkeypatch)
    assert (await _kode(DI.get_document_detail(_req(), UUID(TAGIHAN))))[0] == 404
    assert (await _kode(DI.get_document_detail(_req(), UUID(NOTA))))[0] == 200


@pytest.mark.asyncio
async def test_detail_owner_semua(intake, monkeypatch):
    from uuid import UUID
    _pakai_model_bebas(monkeypatch)
    assert (await _kode(DI.get_document_detail(_req("OWNER"), UUID(TAGIHAN))))[0] == 200


@pytest.mark.asyncio
async def test_antrean_review_disaring_total_jujur(intake, monkeypatch):
    _pakai_model_bebas(monkeypatch)
    kode, b = await _kode(DI.get_review_queue(_req(), status=None, limit=50, offset=0))
    assert kode == 200
    assert [d["doc_type"] for d in b["data"]] == ["receipt"] and b["total"] == 1


@pytest.mark.asyncio
async def test_status_batch_disaring(intake, monkeypatch):
    from uuid import UUID
    _pakai_model_bebas(monkeypatch)
    kode, b = await _kode(DI.get_batch_status(_req(), UUID(BATCH)))
    assert [d["doc_type"] for d in b["documents"]] == ["receipt"]


@pytest.mark.asyncio
async def test_anggota_tanpa_baris_403(intake, monkeypatch):
    from uuid import UUID
    _pakai_model_bebas(monkeypatch)
    assert (await _kode(DI.get_document_detail(_req("NONAKTIF"), UUID(NOTA))))[0] == 403


def test_progres_batch_rincian_disaring():
    import inspect
    src = " ".join(inspect.getsource(DI.get_batch_progress).split())
    assert 'for d in documents if boleh(d["doc_type"])' in src
    assert "boleh = await _penyaring_baca_intake(request)" in src


# ---------- R4 ----------

class _PoolU:
    def __init__(self):
        self.q = []

    async def fetch(self, sql, *a):
        self.q.append((" ".join(sql.split()), a))
        return []


def test_pemakaian_hanya_milik_pemanggil(monkeypatch):
    p = _PoolU()

    async def gp():
        return p

    monkeypatch.setattr(CU, "get_session_db_pool", gp)
    app = FastAPI()
    app.include_router(CU.router, prefix="/api/v3/chat")
    app.dependency_overrides[require_active_membership] = lambda: None
    app.add_middleware(_SetUser)
    r = TestClient(app).get("/api/v3/chat/usage")
    assert r.status_code == 200, r.text
    assert len(p.q) == 2
    for sql, args in p.q:
        assert "session_id IN (SELECT id FROM chat_sessions WHERE tenant_id = $1 AND user_id::text = $3)" in sql
        assert args[2] == USER


@pytest.mark.parametrize("R", [CU, CH, UG])
def test_router_chat_terpisah_berpagar_anggota(R):
    assert any(d.dependency is require_active_membership for d in R.router.dependencies), R.__name__


def test_status_kartu_hanya_pemilik():
    import inspect
    src = " ".join(inspect.getsource(UC.get_action_status).split())
    assert "WHERE id = $1 AND tenant_id = $2 AND user_id = $3" in src


# ---------- R5 ----------

def test_rute_lama_dan_stub_diparkir():
    from app.routers import chat as CHAT, session as SES, device as DEV
    from app.services.fitur_parkir import fitur_belum_tersedia

    def diparkir(router, path, metode="GET"):
        for r in router.routes:
            if r.path == path and metode in r.methods:
                return any(d.call is fitur_belum_tersedia for d in r.dependant.dependencies)
        raise AssertionError(f"rute tak ditemukan: {path}")

    assert diparkir(CHAT.router, "/history")
    assert diparkir(CHAT.router, "/")
    assert diparkir(CHAT.router, "/test")
    assert diparkir(SES.router, "/api/session/list") or diparkir(SES.router, "/list")
    assert diparkir(DEV.router, "/api/devices/stats")
