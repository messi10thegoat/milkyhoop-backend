"""Kuota lampiran beban menghitung lampiran TERSEDIA + medan `tersedia`.

Latar (24 Sep 2026): sesudah recreate 23 Sep, baris `documents` dengan
storage_type='local' = berkas MATI (rute download -> 404). grapgrap
EXP-2609-0020/0021/0022 masing-masing sudah punya 2 baris local mati.
Kuota lama (max 5) menghitung SEMUA baris document_attachments -> baris mati
memakan jatah, sehingga pemilik bisa tertolak saat mengunggah ulang nota.

Kontrak baru:
  - POST /api/expenses/{id}/attachments: kuota 5 hanya menghitung lampiran
    TERSEDIA (documents.storage_type='s3' DAN documents.deleted_at IS NULL).
    display_order tetap = jumlah SEMUA tautan (urutan lama dipertahankan).
  - GET /api/expenses/{id}/attachments (dan respons POST /api/expenses lewat
    helper yang sama): medan `tersedia` = storage_type == 's3'.
Handler dipanggil PENUH dengan pool palsu (pola test_lampiran_1b_download).
"""
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.routers import expenses as ex

TENANT = "kaos-biru-konveksi"
USER_ID = "22222222-2222-2222-2222-222222222222"
BEBAN = uuid.UUID("11111111-1111-1111-1111-111111111111")
DOK_BARU = uuid.UUID("99999999-9999-9999-9999-999999999999")
WAKTU = datetime(2026, 9, 24, tzinfo=timezone.utc)
DIHAPUS = datetime(2026, 9, 20, tzinfo=timezone.utc)


def tautan(storage_type, deleted_at=None, file_path="k/x.jpg"):
    """Satu baris document_attachments x documents milik beban."""
    return {
        "storage_type": storage_type,
        "deleted_at": deleted_at,
        "file_path": file_path,
    }


class DbBeban:
    """DB palsu yang menjawab tiap SQL POST lampiran sesuai SEMANTIKNYA
    terhadap daftar tautan beban -- jadi kode lama (COUNT(*) semua tautan)
    dan kode baru sama-sama dijawab jujur."""

    def __init__(self, tautan_ada):
        self.tautan = tautan_ada
        self.calls = []

    async def execute(self, sql, *args):
        self.calls.append(("execute", sql, args))

    async def fetchrow(self, sql, *args):
        self.calls.append(("fetchrow", sql, args))
        if "FROM expenses" in sql:
            return {"id": BEBAN, "has_receipt": True}
        if "FROM documents" in sql:
            return {"id": DOK_BARU, "file_name": "nota-ulang.jpg"}
        raise AssertionError(f"fetchrow tak dikenal: {sql}")

    async def fetchval(self, sql, *args):
        self.calls.append(("fetchval", sql, args))
        if "COUNT(*)" in sql and "document_attachments" in sql:
            return len(self.tautan)  # semantik SQL lama: semua tautan
        if "SELECT id FROM document_attachments" in sql:
            return None  # belum tertaut
        raise AssertionError(f"fetchval tak dikenal: {sql}")

    async def fetch(self, sql, *args):
        self.calls.append(("fetch", sql, args))
        if "document_attachments" in sql:
            return [dict(t) for t in self.tautan]
        raise AssertionError(f"fetch tak dikenal: {sql}")


class _Ctx:
    def __init__(self, val):
        self.val = val

    async def __aenter__(self):
        return self.val

    async def __aexit__(self, *a):
        return False


def pasang(monkeypatch, conn):
    async def _pool():
        return SimpleNamespace(acquire=lambda: _Ctx(conn))

    monkeypatch.setattr(ex, "get_pool", _pool)


def req():
    return SimpleNamespace(
        state=SimpleNamespace(user={"tenant_id": TENANT, "user_id": USER_ID})
    )


async def unggah(monkeypatch, tautan_ada):
    conn = DbBeban(tautan_ada)
    pasang(monkeypatch, conn)
    body = ex.AddAttachmentRequest(document_id=DOK_BARU)
    out = await ex.add_expense_attachment(req(), BEBAN, body)
    return out, conn


def _insert(conn):
    ins = [c for c in conn.calls if c[0] == "execute" and "INSERT INTO document_attachments" in c[1]]
    assert len(ins) == 1, ins
    return ins[0][2]


# ------------------------------------------------------------- kuota POST
@pytest.mark.asyncio
async def test_kuota_3_local_mati_2_s3_unggah_baru_diterima(monkeypatch):
    """Keadaan grapgrap diperbesar: baris local mati TIDAK memakan jatah."""
    ada = [tautan("local")] * 3 + [tautan("s3")] * 2
    out, conn = await unggah(monkeypatch, ada)
    assert out["success"] is True
    args = _insert(conn)
    # display_order = jumlah SEMUA tautan (5), bukan jumlah tersedia (2):
    # nota baru tetap diurutkan SESUDAH baris lama, seperti sebelumnya.
    assert args[3] == 5
    assert args[2] == str(BEBAN) and args[1] == str(DOK_BARU)


@pytest.mark.asyncio
async def test_kuota_2_local_3_s3_unggah_keempat_diterima(monkeypatch):
    ada = [tautan("local")] * 2 + [tautan("s3")] * 3
    out, conn = await unggah(monkeypatch, ada)
    assert out["success"] is True
    assert _insert(conn)[3] == 5


@pytest.mark.asyncio
async def test_kuota_5_s3_tetap_ditolak(monkeypatch):
    """Kontrol positif: lima lampiran tersedia -> tetap ditolak, tanpa INSERT."""
    ada = [tautan("s3")] * 5
    with pytest.raises(HTTPException) as ei:
        await unggah(monkeypatch, ada)
    assert ei.value.status_code == 400
    assert ei.value.detail == "Maximum 5 attachments per expense"


@pytest.mark.asyncio
async def test_kuota_5_s3_plus_local_tetap_ditolak(monkeypatch):
    ada = [tautan("local")] * 2 + [tautan("s3")] * 5
    out = None
    with pytest.raises(HTTPException) as ei:
        out, _ = await unggah(monkeypatch, ada)
    assert ei.value.status_code == 400, out


@pytest.mark.asyncio
async def test_kuota_s3_terhapus_tak_dihitung(monkeypatch):
    """documents.deleted_at terisi = bukan lampiran tersedia."""
    ada = [tautan("s3")] * 4 + [tautan("s3", deleted_at=DIHAPUS)] + [tautan("local")]
    out, conn = await unggah(monkeypatch, ada)
    assert out["success"] is True
    assert _insert(conn)[3] == 6


@pytest.mark.asyncio
async def test_kuota_dokumen_hilang_tak_dihitung(monkeypatch):
    """Tautan tanpa baris documents (LEFT JOIN -> storage_type NULL) bukan
    lampiran tersedia, tapi tetap dihitung untuk display_order."""
    ada = [tautan("s3")] * 4 + [tautan(None, file_path=None)]
    out, conn = await unggah(monkeypatch, ada)
    assert out["success"] is True
    assert _insert(conn)[3] == 5


@pytest.mark.asyncio
async def test_kuota_sql_membaca_tautan_beban_ini(monkeypatch):
    ada = [tautan("s3")]
    _, conn = await unggah(monkeypatch, ada)
    q = [c for c in conn.calls if c[0] == "fetch"]
    assert len(q) == 1
    sql, args = q[0][1], q[0][2]
    assert "entity_type = 'expense'" in sql
    assert "da.entity_id = $1" in sql
    assert "d.storage_type" in sql and "d.deleted_at" in sql
    assert "LEFT JOIN documents" in sql
    assert args == (str(BEBAN),)


def test_status_beban_tak_diperiksa_di_post_lampiran():
    """Beban TERBIT boleh ditautkan nota: handler tak membaca status."""
    import inspect
    import re

    src = inspect.getsource(ex.add_expense_attachment)
    # `status_code=` (HTTPException) bukan pembacaan status beban
    assert re.findall(r"status(?!_code)", src) == []
    assert "posted" not in src


# ------------------------------------------------------------ `tersedia`
def baris_daftar(i, storage_type, file_url=None):
    return {
        "id": uuid.UUID(int=i),
        "file_name": f"nota{i}.jpg",
        "file_size": 10,
        "mime_type": "image/jpeg",
        "width": None,
        "height": None,
        "file_url": file_url,
        "storage_type": storage_type,
        "uploaded_at": WAKTU,
        "display_order": i,
    }


def test_helper_tersedia_per_storage_type():
    rows = [
        baris_daftar(1, "s3"),  # s3: file_url NULL (keadaan prod)
        baris_daftar(2, "local", file_url="/api/v3/chat/files/x/forms/a.jpg"),
        baris_daftar(3, "local"),
        baris_daftar(4, None),
    ]
    out = ex._exp_lampiran_ke_respons(rows, BEBAN)
    assert [a["tersedia"] for a in out] == [True, False, False, False]
    for a in out:
        assert type(a["tersedia"]) is bool
        assert "storage_type" not in a and "file_url" not in a


@pytest.mark.asyncio
async def test_get_daftar_tersedia(monkeypatch):
    rows = [baris_daftar(1, "local"), baris_daftar(2, "local"), baris_daftar(3, "s3")]

    class Conn:
        async def execute(self, *a):
            pass

        async def fetchval(self, sql, *a):
            return BEBAN

        async def fetch(self, sql, *a):
            assert "d.storage_type" in sql
            return [dict(r) for r in rows]

    pasang(monkeypatch, Conn())
    out = await ex.list_expense_attachments(req(), BEBAN)
    data = out["data"]
    assert [a["tersedia"] for a in data] == [False, False, True]
    assert set(data[0]) == {
        "id", "file_name", "file_size", "mime_type", "width", "height",
        "url", "thumbnail_url", "uploaded_at", "display_order", "tersedia",
    }
    # url tetap ke rute download untuk SEMUA baris (medan lain tak berubah)
    for a in data:
        assert a["url"] == f"/api/expenses/{BEBAN}/attachments/{a['id']}/download"
