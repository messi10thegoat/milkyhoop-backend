"""Audit WRITE_EXEMPT intake (26 Sep 2026) — override draf & jalur jurnal legacy.

Terukur di kode: confirm me-merge `overrides` klien ke draft_plan TANPA batas
lalu langsung mengeksekusi; eksekutor merutekan dari draft_plan.action_type —
yang tak punya rute REST jatuh ke _execute_legacy = INSERT jurnal langsung dari
journal_draft. Izin hanya C pada modul doc_type. Retry tanpa izin modul.
Data: uploaded_documents 0 baris; FE mengirim badan kosong {} (laten).

Kini: I1 overrides tak kosong -> 400 OVERRIDES_NOT_ALLOWED (sebelum apa pun);
I2 jalur legacy wajib izin C journal (confirm/execute/execute-batch/retry);
I3 retry = syarat yang sama dengan execute.
"""
import pytest
from fastapi import FastAPI
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.testclient import TestClient

from app.routers import document_intake as DI
from app.services import policy_engine_client as PEC

DOC = "00000000-0000-0000-0000-0000000000d1"
DOC2 = "00000000-0000-0000-0000-0000000000d2"
USER = "00000000-0000-0000-0000-00000000000a"
REST = "record_expense"  # punya rute REST
LEGACY = "zz_tak_dikenal"  # tanpa rute -> _execute_legacy

IZIN = {
    "STAF": {("C", "receipt")},
    "STAF_JURNAL": {("C", "receipt"), ("C", "journal")},
    "KOSONG": set(),
}


class _Ctx:
    def __init__(self, peran):
        self.peran = peran
        self.membership_active = True
        self.business_role_id = "r"
        self.business_role_code = peran


class _Eng:
    async def get_user_context(self, user_id, tenant_id, subscription_role):
        return _Ctx(subscription_role)

    async def can(self, ctx, aksi, modul):
        return (aksi, modul) in IZIN[ctx.peran]


class _Conn:
    def __init__(self, db):
        self.db = db

    async def fetchval(self, sql, *a):
        d = self.db.get(str(a[0]))
        if d is None:
            return None
        if "action_type" in sql:
            return d["action_type"]
        if "doc_type" in sql:
            return d["doc_type"]
        raise AssertionError(sql)

    async def fetchrow(self, sql, *a):
        d = self.db.get(str(a[0]))
        return {"id": a[0], "status": d["status"]} if d else None

    async def execute(self, sql, *a):
        self.db["_tulis"].append(" ".join(sql.split())[:40])


class _Acq:
    def __init__(self, c):
        self.c = c

    async def __aenter__(self):
        return self.c

    async def __aexit__(self, *a):
        return False


class _Pool:
    def __init__(self, db):
        self.db = db

    def acquire(self):
        return _Acq(_Conn(self.db))


class _SetUser(BaseHTTPMiddleware):
    async def dispatch(self, req, nxt):
        req.state.user = {"user_id": USER, "tenant_id": "t", "role": req.headers["x-peran"]}
        return await nxt(req)


@pytest.fixture
def uji(monkeypatch):
    db = {"_tulis": []}
    catat = {"confirm": 0, "execute": [], "batch": []}

    async def pool():
        return _Pool(db)

    class _Svc:
        async def confirm_document(self, *a, **k):
            catat["confirm"] += 1
            return {"id": DOC, "status": "confirmed"}

    async def svc():
        return _Svc()

    class _Exec:
        def __init__(self, *a, **k):
            pass

        async def execute(self, doc_id, *a):
            catat["execute"].append(doc_id)

            class R:
                success = False
                error = "uji"
            return R()

        async def execute_batch(self, ids, *a):
            catat["batch"].extend(ids)
            return {"results": [], "executed": len(ids)}

    import app.services.kernel_document_executor as KDE
    monkeypatch.setattr(DI, "get_pool", pool)
    monkeypatch.setattr(DI, "_get_service", svc)
    monkeypatch.setattr(PEC, "get_policy_engine", lambda: _Eng())
    monkeypatch.setattr(KDE, "KernelDocumentExecutor", _Exec)
    app = FastAPI()
    app.include_router(DI.router, prefix="/api/document-intake")
    app.add_middleware(_SetUser)
    return TestClient(app, raise_server_exceptions=False), db, catat


def _doc(db, did, aksi, status="draft_ready"):
    db[did] = {"doc_type": "receipt", "action_type": aksi, "status": status}


# ---------- I1 ----------

def test_overrides_ditolak_400_sebelum_apa_pun(uji):
    k, db, catat = uji
    _doc(db, DOC, REST)
    r = k.post(f"/api/document-intake/document/{DOC}/confirm",
               json={"overrides": {"action_type": LEGACY, "journal_draft": {"lines": [{"account_code": "1-10100", "debit": 1e9}]}}},
               headers={"x-peran": "STAF_JURNAL"})
    assert r.status_code == 400, r.text
    assert r.json()["detail"]["code"] == "OVERRIDES_NOT_ALLOWED"
    assert catat["confirm"] == 0 and catat["execute"] == []


@pytest.mark.parametrize("badan", [None, {}, {"overrides": {}}, {"overrides": None}])
def test_badan_kosong_fe_tetap_jalan(uji, badan):
    k, db, catat = uji
    _doc(db, DOC, REST)
    kw = {"json": badan} if badan is not None else {}
    r = k.post(f"/api/document-intake/document/{DOC}/confirm", headers={"x-peran": "STAF"}, **kw)
    assert r.status_code != 400 and r.status_code != 403, r.text
    assert catat["confirm"] == 1


# ---------- I2 ----------

def test_confirm_legacy_tanpa_izin_jurnal_403(uji):
    k, db, catat = uji
    _doc(db, DOC, LEGACY)
    r = k.post(f"/api/document-intake/document/{DOC}/confirm", json={}, headers={"x-peran": "STAF"})
    assert r.status_code == 403
    assert catat["confirm"] == 0 and catat["execute"] == []


def test_confirm_legacy_dengan_izin_jurnal_jalan(uji):
    k, db, catat = uji
    _doc(db, DOC, LEGACY)
    k.post(f"/api/document-intake/document/{DOC}/confirm", json={}, headers={"x-peran": "STAF_JURNAL"})
    assert catat["confirm"] == 1


def test_confirm_action_type_kosong_dianggap_legacy(uji):
    k, db, catat = uji
    _doc(db, DOC, None)
    r = k.post(f"/api/document-intake/document/{DOC}/confirm", json={}, headers={"x-peran": "STAF"})
    assert r.status_code == 403


def test_execute_legacy_tanpa_izin_jurnal_403(uji):
    k, db, catat = uji
    _doc(db, DOC, LEGACY, "confirmed")
    r = k.post(f"/api/document-intake/document/{DOC}/execute", headers={"x-peran": "STAF"})
    assert r.status_code == 403
    assert catat["execute"] == []


def test_execute_rest_cukup_izin_doctype(uji):
    k, db, catat = uji
    _doc(db, DOC, REST, "confirmed")
    k.post(f"/api/document-intake/document/{DOC}/execute", headers={"x-peran": "STAF"})
    assert catat["execute"] == [DOC]


def test_batch_legacy_ditolak_per_item(uji):
    k, db, catat = uji
    _doc(db, DOC, LEGACY, "confirmed")
    _doc(db, DOC2, REST, "confirmed")
    r = k.post("/api/document-intake/execute-batch", json={"document_ids": [DOC, DOC2]}, headers={"x-peran": "STAF"})
    assert r.status_code == 200, r.text
    assert catat["batch"] == [DOC2]
    assert [d["document_id"] for d in r.json()["denied"]] == [DOC]


# ---------- I3 ----------

def test_retry_tanpa_izin_modul_403_tanpa_tulis(uji):
    k, db, catat = uji
    _doc(db, DOC, REST, "posting_failed")
    r = k.post(f"/api/document-intake/document/{DOC}/retry", headers={"x-peran": "KOSONG"})
    assert r.status_code == 403
    assert db["_tulis"] == [] and catat["execute"] == []


def test_retry_legacy_tanpa_izin_jurnal_403(uji):
    k, db, catat = uji
    _doc(db, DOC, LEGACY, "posting_failed")
    r = k.post(f"/api/document-intake/document/{DOC}/retry", headers={"x-peran": "STAF"})
    assert r.status_code == 403
    assert db["_tulis"] == []


def test_retry_berizin_jalan(uji):
    k, db, catat = uji
    _doc(db, DOC, REST, "posting_failed")
    k.post(f"/api/document-intake/document/{DOC}/retry", headers={"x-peran": "STAF"})
    assert catat["execute"] == [DOC]
