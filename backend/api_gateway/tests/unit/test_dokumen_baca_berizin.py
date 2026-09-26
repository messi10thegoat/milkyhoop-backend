"""Audit READ_OPEN R1+R3 (26 Sep 2026) — baca dokumen & berkas mengikuti aturan /download.

Dulu GET /api/documents (daftar/terbaru/cari/detail) = tenant saja, tanpa cek
anggota maupun izin modul, dan `file_url` lama = /api/v3/chat/files/<kunci>
(34 baris prod); rute berkas itu cukup tenant+anggota -> anggota tanpa izin
beban/pembayaran mengambil kunci lalu mengunduh isinya, melewati /download.
Kini: satu aturan _boleh_baca_dokumen (OWNER; R pada entitas tertaut; tanpa
tautan = pengunggah) untuk daftar/terbaru/cari/detail/unduh DAN berkas chat;
file_url selalu jalur /download.
"""
import os
from types import SimpleNamespace

os.environ.setdefault("OPENAI_API_KEY", "sk-boneka-unit-test-tanpa-jaringan")

import pytest  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from starlette.middleware.base import BaseHTTPMiddleware  # noqa: E402
from starlette.testclient import TestClient  # noqa: E402

from app.routers import documents as DOC  # noqa: E402
from app.routers import unified_chat as UC  # noqa: E402
from app.services import policy_engine_client as PEC  # noqa: E402

T = "kaos-biru-konveksi"
STAF = "00000000-0000-0000-0000-0000000000a1"
LAIN = "00000000-0000-0000-0000-0000000000a2"
SHA = {n: (c * 64) for n, c in zip(("beban", "faktur", "milik", "orang", "chat"), "abcde")}
D = {  # id -> (tautan entity_type | None, uploaded_by, sha)
    "11111111-0000-0000-0000-000000000001": ("expense", LAIN, SHA["beban"]),
    "11111111-0000-0000-0000-000000000002": ("sales_invoice", LAIN, SHA["faktur"]),
    "11111111-0000-0000-0000-000000000003": (None, STAF, SHA["milik"]),
    "11111111-0000-0000-0000-000000000004": (None, LAIN, SHA["orang"]),
}
BEBAN, FAKTUR, MILIK, ORANG = list(D)


def _baris(i):
    ent, up, sha = D[i]
    return {"id": i, "file_name": f"{sha}.png", "original_name": "x.png", "file_type": "image/png",
            "file_extension": "png", "file_size": 10, "storage_type": "s3",
            "file_url": f"/api/v3/chat/files/{T}/uploads/forms/{sha}.png",
            "file_path": f"{T}/uploads/forms/{sha}.png", "thumbnail_url": None, "category": None,
            "subcategory": None, "title": "t", "description": None, "tags": None, "width": None,
            "height": None, "processing_status": "completed", "uploaded_at": "2026-09-26T00:00:00",
            "uploaded_by": up, "attachment_count": 1 if ent else 0}


class _Conn:
    def __init__(self):
        self.q = []

    async def execute(self, *a):
        pass

    async def fetchval(self, sql, *a):
        self.q.append(sql)
        if "COUNT(*) FROM documents" in sql:
            return len(D)
        if "FROM chat_attachments" in sql:
            # tiru DB: pemilik sesi = STAF; predikat pemilik hanya berlaku bila ADA di SQL
            if a[1] != f"%/{SHA['chat']}.png":
                return None
            return 1 if ("s.user_id::text = $3" not in sql or a[2] == STAF) else None
        if "FROM uploaded_documents" in sql:
            return None
        raise AssertionError(sql[:60])

    async def fetchrow(self, sql, *a):
        if "FROM documents d" in sql:
            return _baris(str(a[0])) if str(a[0]) in D else None
        raise AssertionError(sql[:60])

    async def fetch(self, sql, *a):
        self.q.append(sql)
        if "FROM documents d" in sql:  # daftar/terbaru (memuat subkueri attachment_count)
            return [_baris(i) for i in D]
        if "SELECT entity_type, entity_id FROM document_attachments" in " ".join(sql.split()):
            ent = D[str(a[0])][0]
            return [{"entity_type": ent, "entity_id": "e1"}] if ent else []
        if "FROM document_attachments" in sql:
            return []
        if "FROM documents" in sql and "LIKE $2" in sql:
            return [{"id": i, "uploaded_by": D[i][1]} for i in D if a[1] == f"%/{D[i][2]}.png"]
        raise AssertionError(sql[:60])


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


class _Eng:
    async def get_user_context(self, user_id, tenant_id, subscription_role):
        peran = "OWNER" if user_id == "OWNER" else "STAFF"
        return SimpleNamespace(membership_active=True, business_role_id="r", business_role_code=peran)


class _SetUser(BaseHTTPMiddleware):
    async def dispatch(self, req, nxt):
        req.state.user = {"user_id": req.headers["x-u"], "tenant_id": T, "role": "ADMIN"}
        return await nxt(req)


@pytest.fixture
def uji(monkeypatch):
    c = _Conn()

    async def pool():
        return _Pool(c)

    async def segmen(conn, et, eid, tid):
        return {"expense": "expenses", "sales_invoice": "sales-invoices"}.get(et)

    async def boleh_entitas(conn, request, eng, ctx, et, seg, eid, aksi):
        # staf: izin R faktur saja, TANPA beban
        return ctx.business_role_code == "OWNER" or et == "sales_invoice"

    served = []

    async def sajikan(storage, tid, key):
        served.append(key)
        return {"ok": key}

    monkeypatch.setattr(PEC, "get_policy_engine", lambda: _Eng())
    monkeypatch.setattr(DOC, "get_pool", pool)
    monkeypatch.setattr(DOC, "_segmen_entitas", segmen)
    monkeypatch.setattr(DOC, "_boleh_entitas", boleh_entitas)
    monkeypatch.setattr(UC, "get_session_db_pool", pool)
    monkeypatch.setattr(UC, "sajikan_objek_unggahan", sajikan)
    monkeypatch.setattr(UC, "get_storage_service", lambda: None)
    app = FastAPI()
    app.include_router(DOC.router, prefix="/api/documents")
    app.include_router(UC.router, prefix="/api/v3/chat")
    app.add_middleware(_SetUser)
    return TestClient(app), c, served


def _ids(r):
    return {d["id"] for d in r.json()["data"]}


def test_daftar_staf_tanpa_dokumen_beban(uji):
    k, c, _ = uji
    r = k.get("/api/documents", headers={"x-u": STAF})
    assert r.status_code == 200, r.text
    assert _ids(r) == {FAKTUR, MILIK}
    assert r.json()["total"] == 2
    assert all(d["file_url"] == f"/api/documents/{d['id']}/download" for d in r.json()["data"])


def test_daftar_owner_utuh(uji):
    k, c, _ = uji
    r = k.get("/api/documents", headers={"x-u": "OWNER"})
    assert _ids(r) == set(D) and r.json()["total"] == len(D)
    assert all("/chat/files/" not in (d["file_url"] or "") for d in r.json()["data"])


def test_terbaru_disaring(uji):
    k, c, _ = uji
    r = k.get("/api/documents/recent", headers={"x-u": STAF})
    assert _ids(r) == {FAKTUR, MILIK}


@pytest.mark.parametrize("doc,harap", [(BEBAN, 404), (ORANG, 404), (FAKTUR, 200), (MILIK, 200)])
def test_detail_staf(uji, doc, harap):
    k, c, _ = uji
    r = k.get(f"/api/documents/{doc}", headers={"x-u": STAF})
    assert r.status_code == harap, r.text
    if harap == 200:
        assert r.json()["data"]["file_url"] == f"/api/documents/{doc}/download"


def _kunci(nama):
    return f"{T}/uploads/forms/{SHA[nama]}.png"


@pytest.mark.parametrize("nama,harap", [("beban", 404), ("orang", 404), ("faktur", 200), ("milik", 200)])
def test_berkas_chat_mengikuti_aturan_dokumen(uji, nama, harap):
    k, c, served = uji
    r = k.get(f"/api/v3/chat/files/{_kunci(nama)}", headers={"x-u": STAF})
    assert r.status_code == harap, r.text
    assert (served != []) == (harap == 200)  # 404 = storage TIDAK disentuh


def test_berkas_lampiran_chat_hanya_pemilik_sesi(uji):
    k, c, served = uji
    kunci = f"{T}/uploads/chat/{SHA['chat']}.png"
    assert k.get(f"/api/v3/chat/files/{kunci}", headers={"x-u": STAF}).status_code == 200
    assert k.get(f"/api/v3/chat/files/{kunci}", headers={"x-u": LAIN}).status_code == 404


def test_berkas_tak_dikenal_404_owner_boleh(uji):
    k, c, served = uji
    kunci = f"{T}/uploads/forms/{'f' * 64}.png"
    assert k.get(f"/api/v3/chat/files/{kunci}", headers={"x-u": STAF}).status_code == 404
    assert k.get(f"/api/v3/chat/files/{kunci}", headers={"x-u": "OWNER"}).status_code == 200


def test_kunci_tak_sah_404_tanpa_db(uji):
    k, c, served = uji
    r = k.get(f"/api/v3/chat/files/{T}/uploads/forms/nota.png", headers={"x-u": STAF})
    assert r.status_code == 404 and c.q == [] and served == []
