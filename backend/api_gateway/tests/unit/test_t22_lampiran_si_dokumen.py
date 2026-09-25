"""#22 — lampiran faktur penjualan dari document_attachments ikut tampil.

Latar (diukur 25 Sep 2026, baca saja): GET /api/sales-invoices/{id}/attachments
hanya membaca `sales_invoice_attachments`. Baris `document_attachments`
entity_type='sales_invoice' (hub /api/documents attach, form lama) tak
terlihat di mana pun — kaos INV-2609-0005 punya 1 baris local. Beban sudah
menampilkan baris serupa dengan `tersedia: false` (FE: label "Berkas hilang").

Kini (pola bills 1b): daftar menggabung sumber kedua + `tersedia` di SEMUA
item; download melayani sumber kedua (local -> 404 "Berkas tidak tersedia");
DELETE baris dokumen = lepas tautan saja (documents + objek tetap). Galat
sumber kedua TIDAK ditelan (bills menelannya -> daftar separuh tampak utuh).
"""
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.responses import StreamingResponse

from app.routers import sales_invoices as si

TENANT = "kaos-biru-konveksi"
USER_ID = "22222222-2222-2222-2222-222222222222"
INV = uuid.UUID("11111111-1111-1111-1111-111111111111")
LAMA = uuid.UUID("33333333-3333-3333-3333-333333333333")
DOK_LOCAL = uuid.UUID("44444444-4444-4444-4444-444444444444")
DOK_S3 = uuid.UUID("55555555-5555-5555-5555-555555555555")
WAKTU = datetime(2026, 9, 25, tzinfo=timezone.utc)


class _Ctx:
    def __init__(self, v):
        self.v = v

    async def __aenter__(self):
        return self.v

    async def __aexit__(self, *a):
        return False


class FakeConn:
    """Menjawab per potongan SQL; mencatat semua panggilan."""

    def __init__(self, lama=True, dok=True, dok_meledak=False, taut=True):
        self.lama, self.dok, self.dok_meledak, self.taut = lama, dok, dok_meledak, taut
        self.calls = []

    async def execute(self, sql, *a):
        self.calls.append(("execute", sql, a))

    async def fetchrow(self, sql, *a):
        self.calls.append(("fetchrow", sql, a))
        if "FROM sales_invoices WHERE id" in sql:
            return {"id": INV}
        if "FROM sales_invoice_attachments sa" in sql:
            if self.lama and a[0] == LAMA:
                return {"id": LAMA, "file_path": "k/lama.jpg", "file_name": "lama.jpg",
                        "file_type": "image/jpeg", "storage_type": "s3"}
            return None
        if "FROM document_attachments da" in sql:
            if not self.dok:
                return None
            st = "local" if a[0] == DOK_LOCAL else "s3"
            return {"file_name": "nota.jpg", "file_path": "k/dok.jpg",
                    "file_type": "image/jpeg", "storage_type": st}
        raise AssertionError(f"SQL tak dikenal: {sql[:80]}")

    async def fetch(self, sql, *a):
        self.calls.append(("fetch", sql, a))
        if "FROM sales_invoice_attachments sa" in sql:
            return [{"id": LAMA, "filename": "lama.jpg", "file_path": "k/lama.jpg",
                     "file_size": 10, "mime_type": "image/jpeg", "uploaded_at": WAKTU,
                     "uploaded_by": USER_ID, "uploaded_by_name": "Ani"}] if self.lama else []
        if "FROM document_attachments da" in sql:
            if self.dok_meledak:
                raise RuntimeError("kueri sumber kedua gagal")
            if not self.dok:
                return []
            return [
                {"id": DOK_LOCAL, "file_name": "nota-lama.jpg", "file_size": 7,
                 "file_type": "image/jpeg", "storage_type": "local", "uploaded_at": WAKTU},
                {"id": DOK_S3, "file_name": "bukti.pdf", "file_size": 9,
                 "file_type": "application/pdf", "storage_type": "s3", "uploaded_at": None},
            ]
        raise AssertionError(f"SQL tak dikenal: {sql[:80]}")

    async def fetchval(self, sql, *a):
        self.calls.append(("fetchval", sql, a))
        assert "DELETE FROM document_attachments" in sql
        return uuid.uuid4() if self.taut else None

    def transaction(self):
        return _Ctx(None)


class FakeStorage:
    def __init__(self):
        self.deleted = []
        self.config = SimpleNamespace(bucket="b")
        self.client = SimpleNamespace(get_object=lambda Bucket, Key: {
            "Body": SimpleNamespace(read=lambda n=-1: b"", close=lambda: None)})

    async def delete_file(self, p):
        self.deleted.append(p)
        return True


@pytest.fixture
def pasang(monkeypatch):
    def _p(conn):
        st = FakeStorage()

        async def _pool():
            return SimpleNamespace(acquire=lambda: _Ctx(conn))

        monkeypatch.setattr(si, "get_pool", _pool)
        monkeypatch.setattr(si, "get_storage_service", lambda: st)
        return st
    return _p


def req():
    return SimpleNamespace(state=SimpleNamespace(user={"tenant_id": TENANT, "user_id": USER_ID}))


# ------------------------------------------------------------------ daftar
@pytest.mark.asyncio
async def test_daftar_menggabung_sumber_kedua_dengan_tersedia(pasang):
    conn = FakeConn()
    pasang(conn)
    r = await si.list_invoice_attachments(req(), INV)
    att = r["attachments"]
    assert [a["id"] for a in att] == [str(LAMA), str(DOK_LOCAL), str(DOK_S3)]
    assert [a["tersedia"] for a in att] == [True, False, True]
    for a in att:
        assert a["url"] == f"/api/sales-invoices/{INV}/attachments/{a['id']}/download"
    assert att[1]["filename"] == "nota-lama.jpg" and att[1]["mime_type"] == "image/jpeg"
    # sumber kedua dipaku tenant + faktur ini
    q = [c for c in conn.calls if c[0] == "fetch" and "document_attachments" in c[1]][0]
    assert q[2] == (TENANT, INV)
    assert "entity_type = 'sales_invoice'" in q[1] and "d.tenant_id = $1" in q[1]


@pytest.mark.asyncio
async def test_daftar_tanpa_sumber_kedua_tetap_sama(pasang):
    pasang(FakeConn(dok=False))
    r = await si.list_invoice_attachments(req(), INV)
    assert [a["id"] for a in r["attachments"]] == [str(LAMA)]
    assert r["attachments"][0]["tersedia"] is True


@pytest.mark.asyncio
async def test_galat_sumber_kedua_tidak_ditelan(pasang):
    pasang(FakeConn(dok_meledak=True))
    with pytest.raises(RuntimeError):
        await si.list_invoice_attachments(req(), INV)


# ------------------------------------------------------------------ unduh
@pytest.mark.asyncio
async def test_unduh_sumber_kedua_s3_distream(pasang):
    conn = FakeConn(lama=False)
    pasang(conn)
    r = await si.download_invoice_attachment(req(), INV, DOK_S3)
    assert isinstance(r, StreamingResponse)
    q = [c for c in conn.calls if "document_attachments" in c[1]][0]
    assert q[2] == (DOK_S3, INV, TENANT)
    for pagar in ("si.tenant_id = $3", "da.tenant_id = $3", "d.tenant_id = $3",
                  "da.entity_type = 'sales_invoice'", "d.deleted_at IS NULL"):
        assert pagar in q[1]


@pytest.mark.asyncio
async def test_unduh_sumber_kedua_local_404_berkas_tak_tersedia(pasang):
    pasang(FakeConn(lama=False))
    with pytest.raises(HTTPException) as e:
        await si.download_invoice_attachment(req(), INV, DOK_LOCAL)
    assert e.value.status_code == 404 and e.value.detail == "Berkas tidak tersedia"


@pytest.mark.asyncio
async def test_unduh_tak_ada_di_kedua_sumber_404(pasang):
    pasang(FakeConn(lama=False, dok=False))
    with pytest.raises(HTTPException) as e:
        await si.download_invoice_attachment(req(), INV, DOK_S3)
    assert e.value.status_code == 404


@pytest.mark.asyncio
async def test_unduh_sumber_lama_tak_menyentuh_sumber_kedua(pasang):
    conn = FakeConn()
    pasang(conn)
    await si.download_invoice_attachment(req(), INV, LAMA)
    assert not [c for c in conn.calls if "document_attachments" in c[1]]


# ------------------------------------------------------------------ hapus
@pytest.mark.asyncio
async def test_hapus_baris_dokumen_lepas_tautan_saja(pasang):
    conn = FakeConn(lama=False)
    st = pasang(conn)
    r = await si.delete_invoice_attachment(req(), INV, DOK_LOCAL)
    assert r["success"] is True
    assert st.deleted == [], "objek/dokumen hub tak boleh dihapus"
    q = [c for c in conn.calls if c[0] == "fetchval"][0]
    assert q[2] == (DOK_LOCAL, INV, TENANT)
    assert "si.tenant_id = $3" in q[1] and "da.tenant_id = $3" in q[1]
    assert "DELETE FROM documents" not in q[1]


@pytest.mark.asyncio
async def test_hapus_tak_tertaut_404(pasang):
    pasang(FakeConn(lama=False, taut=False))
    with pytest.raises(HTTPException) as e:
        await si.delete_invoice_attachment(req(), INV, DOK_S3)
    assert e.value.status_code == 404


@pytest.mark.asyncio
async def test_hapus_sumber_lama_tetap_menghapus_objek(pasang):
    conn = FakeConn()
    st = pasang(conn)
    await si.delete_invoice_attachment(req(), INV, LAMA)
    assert st.deleted == ["k/lama.jpg"]
    assert not [c for c in conn.calls if c[0] == "fetchval"]
