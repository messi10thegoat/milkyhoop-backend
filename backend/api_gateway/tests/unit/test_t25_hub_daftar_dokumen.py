"""#25 — hub GET /api/documents/{entity_type}/{entity_id}/documents.

Latar (diukur 25 Sep 2026, baca saja):
  - fungsi DB get_entity_documents tak mengembalikan display_order, sedangkan
    EntityDocument mewajibkannya -> 500 untuk SETIAP entitas berlampiran;
  - respons entity_type ber-Literal tanpa customer_deposit -> 500 walau kosong;
    kategori 'unclassified' (diizinkan CHECK DB) di luar Literal -> 500;
  - file_url = nilai mentah DB (baris local = path berkas-chat mati);
  - TANPA izin per-doctype (READ /api/documents default-open): memperbaiki 500
    saja = membuka dokumen modul mana pun (termasuk karyawan) ke semua anggota;
  - unduh hub: get_object dengan kunci baris local -> 500.
nginx 14 hari: 0 panggilan daftar (laten), unduh hub 10x 200.
"""
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.responses import StreamingResponse

from app.routers import documents as DOC

TENANT = "kaos-biru-konveksi"
USER_ID = "22222222-2222-2222-2222-222222222222"
INDUK = uuid.UUID("11111111-1111-1111-1111-111111111111")
D_S3 = uuid.UUID("55555555-5555-5555-5555-555555555555")
D_LOCAL = uuid.UUID("44444444-4444-4444-4444-444444444444")
WAKTU = datetime(2026, 9, 25, tzinfo=timezone.utc)


class _Ctx:
    def __init__(self, v=None):
        self.v = v

    async def __aenter__(self):
        return self.v

    async def __aexit__(self, *a):
        return False


class Conn:
    def __init__(self, ada=True, rows=None):
        self.ada = ada
        self.rows = rows if rows is not None else [
            {"document_id": D_S3, "file_name": "bukti.pdf", "file_type": "application/pdf",
             "file_size": 9, "category": "unclassified", "title": None, "uploaded_at": WAKTU,
             "storage_type": "s3", "attachment_type": "attachment", "display_order": 0},
            {"document_id": D_LOCAL, "file_name": "nota.jpg", "file_type": "image/jpeg",
             "file_size": 7, "category": "receipt", "title": None, "uploaded_at": WAKTU,
             "storage_type": "local", "attachment_type": None, "display_order": None},
        ]
        self.calls = []

    async def execute(self, sql, *a):
        self.calls.append(("execute", sql, a))

    async def fetchval(self, sql, *a):
        self.calls.append(("fetchval", sql, a))
        return 1 if self.ada else None

    async def fetch(self, sql, *a):
        self.calls.append(("fetch", sql, a))
        assert "get_entity_documents(" not in sql
        return self.rows

    async def fetchrow(self, sql, *a):
        self.calls.append(("fetchrow", sql, a))
        st = "local" if a[0] == D_LOCAL else "s3"
        return {"file_name": "x.pdf", "file_path": "k/x.pdf", "file_type": "application/pdf",
                "storage_type": st}

    def transaction(self):
        return _Ctx()


class Eng:
    def __init__(self, peran, izin=(), aktif=True):
        self.peran, self.izin, self.aktif = peran, set(izin), aktif

    async def get_user_context(self, uid, tid, role):
        return SimpleNamespace(business_role_code=self.peran, membership_active=self.aktif)

    async def can(self, c, aksi, modul):
        return (modul, aksi) in self.izin


def pasang(monkeypatch, conn, eng):
    async def _pool():
        return SimpleNamespace(acquire=lambda: _Ctx(conn))

    monkeypatch.setattr(DOC, "get_pool", _pool)
    from app.services import policy_engine_client as pec

    monkeypatch.setattr(pec, "get_policy_engine", lambda: eng)
    st = SimpleNamespace(config=SimpleNamespace(bucket="b"), client=SimpleNamespace(
        get_object=lambda Bucket, Key: {"Body": SimpleNamespace(read=lambda n=-1: b"", close=lambda: None)}))
    monkeypatch.setattr(DOC, "get_storage_service", lambda: st)


def req():
    return SimpleNamespace(state=SimpleNamespace(user={"tenant_id": TENANT, "user_id": USER_ID, "role": "USER"}))


def modul_si():
    return DOC._izin_modul("sales-invoices", INDUK)[0]


# ------------------------------------------------------------------ daftar
@pytest.mark.asyncio
async def test_daftar_200_bentuk_dan_url_rute_unduh(monkeypatch):
    conn = Conn()
    pasang(monkeypatch, conn, Eng("OWNER"))
    r = await DOC.get_entity_documents(req(), "sales_invoice", INDUK)
    assert r.total == 2
    a, b = r.data
    assert a.file_url == f"/api/documents/{D_S3}/download" and a.tersedia is True
    assert b.file_url == f"/api/documents/{D_LOCAL}/download" and b.tersedia is False
    assert a.category == "unclassified"
    assert b.display_order == 0 and b.attachment_type == "attachment"
    q = [c for c in conn.calls if c[0] == "fetch"][0]
    assert q[2] == (TENANT, "sales_invoice", INDUK)
    assert "da.tenant_id = $1" in q[1] and "d.tenant_id = $1" in q[1]
    assert "display_order" in q[1] and "d.deleted_at IS NULL" in q[1]


@pytest.mark.asyncio
async def test_daftar_kosong_200(monkeypatch):
    pasang(monkeypatch, Conn(rows=[]), Eng("OWNER"))
    r = await DOC.get_entity_documents(req(), "sales_invoice", INDUK)
    assert r.total == 0 and r.data == []


@pytest.mark.asyncio
async def test_jenis_tak_didukung_422(monkeypatch):
    pasang(monkeypatch, Conn(), Eng("OWNER"))
    with pytest.raises(HTTPException) as e:
        await DOC.get_entity_documents(req(), "bukan_entitas", INDUK)
    assert e.value.status_code == 422


@pytest.mark.asyncio
async def test_entitas_tak_ada_atau_tenant_lain_404(monkeypatch):
    conn = Conn(ada=False)
    pasang(monkeypatch, conn, Eng("OWNER"))
    with pytest.raises(HTTPException) as e:
        await DOC.get_entity_documents(req(), "sales_invoice", INDUK)
    assert e.value.status_code == 404
    assert not [c for c in conn.calls if c[0] == "fetch"]


@pytest.mark.asyncio
async def test_staf_tanpa_izin_baca_403_tanpa_kueri(monkeypatch):
    conn = Conn()
    pasang(monkeypatch, conn, Eng("STAFF", izin=[(modul_si(), "C")]))
    with pytest.raises(HTTPException) as e:
        await DOC.get_entity_documents(req(), "sales_invoice", INDUK)
    assert e.value.status_code == 403
    assert not [c for c in conn.calls if c[0] == "fetch"]


@pytest.mark.asyncio
async def test_staf_dengan_izin_baca_lolos(monkeypatch):
    pasang(monkeypatch, Conn(), Eng("STAFF", izin=[(modul_si(), "R")]))
    r = await DOC.get_entity_documents(req(), "sales_invoice", INDUK)
    assert r.total == 2


@pytest.mark.asyncio
async def test_karyawan_hanya_owner(monkeypatch):
    modul_kar = DOC._izin_modul("employees", INDUK)
    izin = [(modul_kar[0], "R")] if modul_kar else []
    pasang(monkeypatch, Conn(), Eng("ADMIN", izin=izin))
    with pytest.raises(HTTPException) as e:
        await DOC.get_entity_documents(req(), "employee", INDUK)
    assert e.value.status_code == 403
    pasang(monkeypatch, Conn(), Eng("OWNER"))
    r = await DOC.get_entity_documents(req(), "employee", INDUK)
    assert r.total == 2


@pytest.mark.asyncio
async def test_keanggotaan_tak_aktif_403(monkeypatch):
    pasang(monkeypatch, Conn(), Eng("OWNER", aktif=False))
    with pytest.raises(HTTPException) as e:
        await DOC.get_entity_documents(req(), "sales_invoice", INDUK)
    assert e.value.status_code == 403


# ------------------------------------------------------------------ unduh
@pytest.mark.asyncio
async def test_unduh_hub_baris_local_404_bukan_500(monkeypatch):
    pasang(monkeypatch, Conn(), Eng("OWNER"))
    with pytest.raises(HTTPException) as e:
        await DOC.download_document(req(), D_LOCAL)
    assert e.value.status_code == 404 and e.value.detail == "Berkas tidak tersedia"


@pytest.mark.asyncio
async def test_unduh_hub_s3_stream_sajian_aman(monkeypatch):
    pasang(monkeypatch, Conn(), Eng("OWNER"))
    r = await DOC.download_document(req(), D_S3)
    assert isinstance(r, StreamingResponse)
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.media_type == "application/pdf"
