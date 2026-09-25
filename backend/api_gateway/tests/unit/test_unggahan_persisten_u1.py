"""Unit U1 -- unggahan PERSISTEN (24 Sep 2026).

Latar (diukur 24 Sep, master ea203072): unggahan form (POST
/api/uploads/document) dan chat (_store_upload_file) ditulis ke
/tmp/milkyhoop_uploads/<tenant>/(forms|chat)/ di DALAM kontainer (tak
di-mount) -> hilang tiap recreate. 30 baris documents 'local' + 17
chat_attachments = referensi mati. Respons chat membocorkan stored_path
absolut ke klien.

Kini: objek MinIO berkunci `<tenant>/uploads/<forms|chat>/<sha256><ext>`,
baris documents 's3', GET /api/v3/chat/files/{kunci} men-stream dari MinIO
tanpa disk. Handler dipanggil PENUH dengan pool/storage palsu (pola 1a/1b).

Impor unified_chat KERAS (bukan lunak): kegagalan impor = merah, bukan skip.
"""
import asyncio
import hashlib
import io
import os
import re
import threading
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.responses import StreamingResponse
from starlette.datastructures import Headers, UploadFile

os.environ.setdefault("OPENAI_API_KEY", "sk-boneka-unit-test-tanpa-jaringan")

from app.routers import unified_chat as UC  # noqa: E402
from app.routers import uploads as UP  # noqa: E402
from app.utils import lampiran_unduh as lu  # noqa: E402

TENANT = "kaos-biru-konveksi"
TENANT_LAIN = "tenant-lain"
USER_ID = "22222222-2222-2222-2222-222222222222"
BUCKET = "milkyhoop-documents"
ISI_PNG = b"\x89PNG\r\n\x1a\n" + b"isi-nota" * 20000  # > 64 KiB
SHA_PNG = hashlib.sha256(ISI_PNG).hexdigest()
SUMBER_UC = Path(__file__).resolve().parents[2] / "app/routers/unified_chat.py"


# ----------------------------------------------------------------- palsu
class ObjekHilang(Exception):
    def __init__(self):
        super().__init__("NoSuchKey")
        self.response = {"Error": {"Code": "NoSuchKey"}}


class FakeBody:
    def __init__(self, data):
        self._b = io.BytesIO(data)
        self.closed = False

    def read(self, n=-1):
        return self._b.read(n)

    def close(self):
        self.closed = True


class FakeClient:
    def __init__(self):
        self.objek = {}
        self.put_calls = []
        self.get_calls = []
        self.utas = []  # (operasi, thread id) -- boto3 sinkron wajib di threadpool

    def put_object(self, **kw):
        self.utas.append(("put", threading.get_ident()))
        self.put_calls.append(kw)
        self.objek[(kw["Bucket"], kw["Key"])] = kw["Body"]
        return {}

    def get_object(self, Bucket, Key):
        self.utas.append(("get", threading.get_ident()))
        self.get_calls.append((Bucket, Key))
        if (Bucket, Key) not in self.objek:
            raise ObjekHilang()
        return {"Body": FakeBody(self.objek[(Bucket, Key)])}


class FakeStorage:
    def __init__(self):
        self.client = FakeClient()
        self.config = SimpleNamespace(bucket=BUCKET)

    # Jalur lama yang TIDAK boleh dipakai (whitelist tipe, kunci uuid,
    # metadata mentah) -- kalau terpanggil, tes gagal keras.
    async def upload_file(self, *a, **k):  # pragma: no cover
        raise AssertionError("upload_file tak boleh dipakai unggahan U1")

    async def generate_signed_url(self, *a, **k):  # pragma: no cover
        raise AssertionError("presign tak boleh dipakai unggahan U1")


def _norm(sql):
    return " ".join(sql.split())


class FakeDocsDB:
    """Tabel documents mini. SELECT dedup dievaluasi MENURUT teks SQL-nya:
    saringan storage_type/file_path hanya berlaku bila SQL memuatnya -- jadi
    menghapus saringan dari SQL produksi = baris local ikut kembali."""

    def __init__(self, rows=None):
        self.rows = list(rows or [])
        self.calls = []

    async def execute(self, sql, *args):
        self.calls.append(("execute", sql, args))

    async def fetchrow(self, sql, *args):
        self.calls.append(("fetchrow", sql, args))
        n = _norm(sql)
        if "FROM documents" not in n:
            return None
        k = [
            r for r in self.rows
            if r["tenant_id"] == args[0]
            and r["checksum_sha256"] == args[1]
            and r.get("deleted_at") is None
        ]
        if "storage_type = 's3'" in n:
            k = [r for r in k if r["storage_type"] == "s3"]
        if "file_path = $3" in n:
            k = [r for r in k if r["file_path"] == args[2]]
        return {"id": k[0]["id"], "file_url": k[0].get("file_url")} if k else None

    async def fetchval(self, sql, *args):
        self.calls.append(("fetchval", sql, args))
        n = _norm(sql)
        assert n.startswith("INSERT INTO documents"), n
        baru = uuid.uuid4()
        kol = re.search(r"\((.*?)\) VALUES \((.*?)\)", n)
        nama = [c.strip() for c in kol.group(1).split(",")]
        nilai = [v.strip() for v in kol.group(2).split(",")]
        row = {"id": baru, "deleted_at": None}
        for c, v in zip(nama, nilai):
            m = re.match(r"\$(\d+)", v)
            row[c] = args[int(m.group(1)) - 1] if m else v.strip("'")
        self.rows.append(row)
        return baru

    def transaction(self):
        return _Ctx(None)

    @property
    def inserts(self):
        return [c for c in self.calls if c[0] == "fetchval"]


class _Ctx:
    def __init__(self, val):
        self.val = val

    async def __aenter__(self):
        return self.val

    async def __aexit__(self, *a):
        return False


class FakePool:
    def __init__(self, conn):
        self.conn = conn

    def acquire(self):
        return _Ctx(self.conn)

    async def execute(self, sql, *args):
        return await self.conn.execute(sql, *args)

    async def fetchrow(self, sql, *args):
        return await self.conn.fetchrow(sql, *args)


def req(tenant=TENANT):
    return SimpleNamespace(
        state=SimpleNamespace(user={"tenant_id": tenant, "user_id": USER_ID}),
        headers={},
    )


def berkas(nama="nota.png", tipe="image/png", isi=ISI_PNG):
    return UploadFile(
        file=io.BytesIO(isi), filename=nama, headers=Headers({"content-type": tipe})
    )


def isi_dir(p: Path):
    return sorted(str(x.relative_to(p)) for x in p.rglob("*"))


@pytest.fixture
def form(monkeypatch, tmp_path):
    """Pasang pool/storage palsu ke routers/uploads + UPLOAD_BASE_DIR -> tmp."""
    db = FakeDocsDB()
    storage = FakeStorage()

    async def _pool():
        return FakePool(db)

    monkeypatch.setattr(UP, "get_session_db_pool", _pool)
    monkeypatch.setattr(UP, "get_storage_service", lambda: storage)
    # kode lama menulis ke UP.UPLOAD_BASE_DIR -> arahkan ke tmp lalu assert kosong
    monkeypatch.setattr(UP, "UPLOAD_BASE_DIR", str(tmp_path), raising=False)
    # I4 (26 Sep 2026): /api/uploads/document kini menuntut anggota AKTIF (baris peran ada).
    import app.services.policy_engine_client as _PEC

    class _EngAktif:
        async def get_user_context(self, *a, **k):
            return SimpleNamespace(membership_active=True, business_role_id="peran-uji", business_role_code="STAFF")

    monkeypatch.setattr(_PEC, "get_policy_engine", lambda: _EngAktif())
    return SimpleNamespace(db=db, storage=storage, tmp=tmp_path)


@pytest.fixture
def chat(monkeypatch, tmp_path):
    db = FakeDocsDB()
    storage = FakeStorage()
    monkeypatch.setattr(UC, "get_storage_service", lambda: storage)
    # U2: UPLOAD_BASE_DIR dihapus dari unified_chat; kode lama (tembolok U1)
    # menulis ke sini -> tetap diarahkan ke tmp supaya tulisan terlihat.
    monkeypatch.setattr(UC, "UPLOAD_BASE_DIR", str(tmp_path), raising=False)
    return SimpleNamespace(db=db, pool=FakePool(db), storage=storage, tmp=tmp_path)


def kunci(sub, sha=SHA_PNG, ext=".png", tenant=TENANT):
    return f"{tenant}/uploads/{sub}/{sha}{ext}"


async def baca_stream(resp):
    potong = []
    async for c in resp.body_iterator:
        potong.append(c if isinstance(c, bytes) else c.encode())
    return b"".join(potong)


# ======================================= 1. unggah form -> MinIO, bukan disk
@pytest.mark.asyncio
async def test_form_unggah_ke_minio_baris_s3_tanpa_disk(form):
    out = await UP.upload_document_for_form(req(), berkas())
    k = kunci("forms")
    assert [c["Key"] for c in form.storage.client.put_calls] == [k]
    put = form.storage.client.put_calls[0]
    assert put["Bucket"] == BUCKET and put["Body"] == ISI_PNG
    assert put["ContentType"] == "image/png"
    assert form.storage.client.objek[(BUCKET, k)] == ISI_PNG
    assert len(form.db.inserts) == 1
    row = form.db.rows[-1]
    assert row["storage_type"] == "s3" and row["file_path"] == k
    assert row["source"] == "form" and row["checksum_sha256"] == SHA_PNG
    assert out["data"]["id"] == str(row["id"])
    assert isi_dir(form.tmp) == []  # TIDAK ada tulisan ke disk
    assert not os.path.exists(f"/tmp/milkyhoop_uploads/{TENANT}/forms/{SHA_PNG}.png")


# =================== 7. respons form: url gateway relatif, tidak NULL/presign
@pytest.mark.asyncio
async def test_form_respons_url_gateway_relatif(form):
    out = await UP.upload_document_for_form(req(), berkas())
    url = out["data"]["url"]
    assert url == "/api/v3/chat/files/" + kunci("forms")
    assert url.startswith("/api/") and ":9000" not in url and "http" not in url
    assert form.db.rows[-1]["file_url"] == url


@pytest.mark.asyncio
async def test_form_ext_dari_tipe_bukan_nama_berkas(form):
    """Nama mentah tak menentukan ext kunci: 'x.html' bertipe png -> .png."""
    await UP.upload_document_for_form(req(), berkas(nama="x.html"))
    await UP.upload_document_for_form(
        req(), berkas(nama="tanpa-ext", tipe="image/jpeg", isi=b"jpg-isi")
    )
    keys = [c["Key"] for c in form.storage.client.put_calls]
    assert keys[0] == kunci("forms")
    assert keys[1] == kunci("forms", hashlib.sha256(b"jpg-isi").hexdigest(), ".jpg")


# ===================================== 4. jebakan dedup (form)
@pytest.mark.asyncio
async def test_form_dedup_abaikan_baris_local_mati(form):
    id_lama = uuid.uuid4()
    form.db.rows.append({
        "id": id_lama, "tenant_id": TENANT, "checksum_sha256": SHA_PNG,
        "storage_type": "local", "deleted_at": None,
        "file_path": f"{TENANT}/forms/{SHA_PNG}.png",
        "file_url": f"/api/v3/chat/files/{TENANT}/forms/{SHA_PNG}.png",
    })
    out = await UP.upload_document_for_form(req(), berkas())
    assert out["data"]["id"] != str(id_lama)
    assert len(form.db.inserts) == 1 and form.db.rows[-1]["storage_type"] == "s3"
    assert out["data"]["url"] == "/api/v3/chat/files/" + kunci("forms")


@pytest.mark.asyncio
async def test_form_dedup_ke_baris_s3_yang_ada(form):
    out1 = await UP.upload_document_for_form(req(), berkas())
    out2 = await UP.upload_document_for_form(req(), berkas(nama="nama-lain.png"))
    assert out2["data"]["id"] == out1["data"]["id"]
    assert len(form.db.inserts) == 1
    assert out2["data"]["url"] == out1["data"]["url"]


@pytest.mark.asyncio
async def test_form_dedup_abaikan_baris_s3_kunci_lain_url_null(form):
    """Baris s3 dari /api/documents (kunci uuid, file_url NULL) bukan target
    dedup -- kalau dipakai, url respons = NULL."""
    form.db.rows.append({
        "id": uuid.uuid4(), "tenant_id": TENANT, "checksum_sha256": SHA_PNG,
        "storage_type": "s3", "deleted_at": None,
        "file_path": f"{TENANT}/other/2026/09/abc_nota.png", "file_url": None,
    })
    out = await UP.upload_document_for_form(req(), berkas())
    assert out["data"]["url"] == "/api/v3/chat/files/" + kunci("forms")
    assert len(form.db.inserts) == 1


# ===================================== 6. nama non-ASCII
@pytest.mark.parametrize("nama", ["nota-bébé-ñ.jpg", "ꦤꦺꦴꦠ.jpg", "فاتورة.jpg"])
@pytest.mark.asyncio
async def test_form_nama_non_ascii(form, nama):
    out = await UP.upload_document_for_form(req(), berkas(nama=nama, tipe="image/jpeg"))
    assert out["success"] is True and out["data"]["file_name"] == nama
    put = form.storage.client.put_calls[0]
    assert put["Key"].isascii()
    for v in (put.get("Metadata") or {}).values():
        assert str(v).isascii(), put
    assert nama not in repr(put)
    assert form.db.rows[-1]["original_name"] == nama  # nama asli tinggal di DB


@pytest.mark.parametrize("nama", ["nota-bébé-ñ.jpg", "ꦤꦺꦴꦠ.jpg", "فاتورة.jpg"])
@pytest.mark.asyncio
async def test_chat_nama_non_ascii(chat, nama):
    fm = await UC._store_upload_file(berkas(nama=nama, tipe="image/jpeg"), TENANT, chat.pool)
    put = chat.storage.client.put_calls[0]
    assert put["Key"] == kunci("chat", ext=".jpg") and put["Key"].isascii()
    for v in (put.get("Metadata") or {}).values():
        assert str(v).isascii(), put
    assert nama not in repr(put)
    assert fm["document_id"]


# ======================================= 1. unggah chat -> MinIO
@pytest.mark.asyncio
async def test_chat_unggah_ke_minio_baris_s3(chat):
    fm = await UC._store_upload_file(berkas(), TENANT, chat.pool)
    k = kunci("chat")
    assert [c["Key"] for c in chat.storage.client.put_calls] == [k]
    assert chat.storage.client.objek[(BUCKET, k)] == ISI_PNG
    row = chat.db.rows[-1]
    assert row["storage_type"] == "s3" and row["file_path"] == k
    assert row["source"] == "chat" and row["file_url"] == "/api/v3/chat/files/" + k
    assert fm["storage_key"] == k and fm["document_id"] == str(row["id"])


@pytest.mark.asyncio
async def test_chat_nol_tulisan_disk(chat):
    """U2 (dibalik dari tembolok U1): unggah chat = NOL tulisan disk, tanpa
    saklar apa pun, dan tetap lengkap (objek + baris + isi memori)."""
    assert not hasattr(UC, "TEMBOLOK_DISK_PEMBACA_TERTUNDA")
    fm = await UC._store_upload_file(berkas(), TENANT, chat.pool)
    assert isi_dir(chat.tmp) == []
    assert chat.storage.client.objek[(BUCKET, kunci("chat"))] == ISI_PNG
    assert fm["_isi"] == ISI_PNG


@pytest.mark.asyncio
async def test_chat_nol_tulisan_disk_di_semua_lokasi(chat, monkeypatch):
    """Bukan hanya tmp yang dipantau: SETIAP open() mode tulis / makedirs /
    replace selama unggah chat dicatat -> harus nol (termasuk path lama
    /tmp/milkyhoop_uploads bila UPLOAD_BASE_DIR tak bisa dialihkan)."""
    import builtins

    tulis = []
    asli_open, asli_makedirs, asli_replace = builtins.open, os.makedirs, os.replace

    def _open(p, mode="r", *a, **k):
        if any(c in mode for c in "wax+"):
            tulis.append(("open", str(p)))
        return asli_open(p, mode, *a, **k)

    monkeypatch.setattr(builtins, "open", _open)
    monkeypatch.setattr(os, "makedirs", lambda p, *a, **k: tulis.append(("makedirs", str(p))) or asli_makedirs(p, *a, **k))
    monkeypatch.setattr(os, "replace", lambda a_, b_: tulis.append(("replace", str(b_))) or asli_replace(a_, b_))
    await UC._store_upload_file(berkas(), TENANT, chat.pool)
    await UC._store_upload_file(berkas(nama="lain.jpg", tipe="image/jpeg", isi=b"jpg"), TENANT, chat.pool)
    assert tulis == []
    assert "stored_path" not in (await UC._store_upload_file(berkas(), TENANT, chat.pool))


@pytest.mark.asyncio
async def test_chat_objek_gagal_galat_generik(chat):
    def _meledak(**kw):
        raise RuntimeError("EndpointConnectionError http://127.0.0.1:9000 bucket x")

    chat.storage.client.put_object = _meledak
    with pytest.raises(RuntimeError) as e:
        await UC._store_upload_file(berkas(), TENANT, chat.pool)
    assert "9000" not in str(e.value) and "http" not in str(e.value)
    assert chat.db.inserts == []  # tak ada baris s3 tanpa objek


@pytest.mark.asyncio
async def test_form_objek_gagal_503_tanpa_baris(form):
    def _meledak(**kw):
        raise RuntimeError("EndpointConnectionError http://127.0.0.1:9000")

    form.storage.client.put_object = _meledak
    with pytest.raises(HTTPException) as e:
        await UP.upload_document_for_form(req(), berkas())
    assert e.value.status_code == 503 and "9000" not in str(e.value.detail)
    assert form.db.inserts == []


# ===================================== 4. jebakan dedup (chat)
@pytest.mark.asyncio
async def test_chat_dedup_abaikan_baris_local_mati(chat):
    id_lama = uuid.uuid4()
    chat.db.rows.append({
        "id": id_lama, "tenant_id": TENANT, "checksum_sha256": SHA_PNG,
        "storage_type": "local", "deleted_at": None,
        "file_path": f"{TENANT}/chat/{SHA_PNG}.png",
    })
    fm = await UC._store_upload_file(berkas(), TENANT, chat.pool)
    assert fm["document_id"] != str(id_lama)
    assert chat.db.rows[-1]["storage_type"] == "s3"


@pytest.mark.asyncio
async def test_chat_dedup_ke_baris_s3_yang_ada(chat):
    fm1 = await UC._store_upload_file(berkas(), TENANT, chat.pool)
    fm2 = await UC._store_upload_file(berkas(), TENANT, chat.pool)
    assert fm2["document_id"] == fm1["document_id"]
    assert len(chat.db.inserts) == 1


@pytest.mark.asyncio
async def test_chat_kunci_advisory_lingkup_transaksi(chat):
    await UC._store_upload_file(berkas(), TENANT, chat.pool)
    sqls = [_norm(c[1]) for c in chat.db.calls if c[0] == "execute"]
    assert any("pg_advisory_xact_lock" in s for s in sqls)
    assert not any("pg_advisory_lock(" in s or "pg_advisory_unlock" in s for s in sqls)


# ===================================== chat_attachments.storage_key
@pytest.mark.asyncio
async def test_chat_attachments_storage_key_kunci_minio(chat):
    fm = await UC._store_upload_file(berkas(), TENANT, chat.pool)

    class PoolAtt:
        def __init__(self):
            self.exec = []

        async def fetchrow(self, sql, *a):
            return {"id": uuid.uuid4()}

        async def execute(self, sql, *a):
            self.exec.append((sql, a))

    p = PoolAtt()
    out = await UC._save_chat_attachments(p, TENANT, str(uuid.uuid4()), [fm])
    assert p.exec[0][1][-1] == kunci("chat")
    assert out[0]["storage_key"] == kunci("chat")
    assert "/tmp/" not in repr(out)


# ================== 7. respons chat tanpa path absolut / isi berkas
@pytest.mark.asyncio
async def test_respons_chat_tanpa_stored_path(chat):
    fm = await UC._store_upload_file(berkas(), TENANT, chat.pool)
    assert "stored_path" not in fm  # U2: path disk tak ada lagi, bahkan internal
    out = UC._berkas_terunggah_respons([fm])
    assert out == [{
        "filename": "nota.png", "size": len(ISI_PNG), "extension": ".png",
        "file_hash": SHA_PNG,
    }]
    assert "/tmp/" not in repr(out) and "stored_path" not in repr(out)


def test_respons_chat_memakai_pembentuk_tanpa_stored_path():
    teks = SUMBER_UC.read_text(encoding="utf-8")
    assert 'response.data["uploaded_files"] = _berkas_terunggah_respons(file_metas)' in teks
    assert '"stored_path": fm["stored_path"]' not in teks


# ================== pembaca SINKRON: isi dari memori, bukan disk
@pytest.mark.asyncio
async def test_blok_gambar_vision_dari_memori_tanpa_disk(chat, monkeypatch):
    fm = await UC._store_upload_file(berkas(), TENANT, chat.pool)
    assert isi_dir(chat.tmp) == []
    blok = UC._build_image_content_blocks("halo", [fm])
    assert blok is not None and blok[1]["type"] == "image_url"


def test_pembaca_sinkron_tak_membuka_stored_path():
    teks = SUMBER_UC.read_text(encoding="utf-8")
    i = teks.index("async def send_message_with_files(")
    j = teks.index("\n@router.", i)
    badan = teks[i:j]
    assert 'open(_intent_img["stored_path"]' not in badan
    assert "isi_atau_none(_intent_img)" in badan
    assert "isi_atau_none(_fm)" in badan
    assert not re.search(r"open\(\s*_stored_path", badan)


# ======================================= 2/3. GET chat/files dari MinIO
async def _get(storage, k, tenant=TENANT):
    return await UC.get_chat_file(req(tenant), k)


@pytest.mark.asyncio
async def test_get_kunci_baru_200_dari_storage_tanpa_disk(chat):
    fm = await UC._store_upload_file(berkas(), TENANT, chat.pool)
    for p in sorted(chat.tmp.rglob("*"), reverse=True):  # buang tembolok disk
        p.unlink() if p.is_file() else p.rmdir()
    resp = await _get(chat.storage, fm["storage_key"])
    assert isinstance(resp, StreamingResponse)
    assert await baca_stream(resp) == ISI_PNG
    assert resp.media_type == "image/png"
    assert resp.headers["x-content-type-options"] == "nosniff"
    assert "content-disposition" not in resp.headers  # png inline
    assert chat.storage.client.get_calls == [(BUCKET, fm["storage_key"])]


@pytest.mark.asyncio
async def test_get_kunci_form_200(chat):
    k = kunci("forms", ext=".pdf")
    chat.storage.client.objek[(BUCKET, k)] = b"%PDF-1.4 x"
    resp = await _get(chat.storage, k)
    assert await baca_stream(resp) == b"%PDF-1.4 x"
    assert resp.media_type == "application/pdf"


@pytest.mark.asyncio
async def test_get_non_inline_diunduh_octet(chat):
    k = kunci("chat", ext=".csv")
    chat.storage.client.objek[(BUCKET, k)] = b"a,b\n1,2\n"
    resp = await _get(chat.storage, k)
    assert resp.media_type == "application/octet-stream"
    assert resp.headers["content-disposition"].startswith("attachment;")
    assert resp.headers["x-content-type-options"] == "nosniff"


@pytest.mark.asyncio
async def test_get_objek_hilang_404(chat):
    with pytest.raises(HTTPException) as e:
        await _get(chat.storage, kunci("chat"))
    assert e.value.status_code == 404


@pytest.mark.parametrize(
    "k",
    [
        kunci("chat", tenant=TENANT_LAIN),                      # tenant lain
        f"{TENANT}/uploads/chat/../chat/{SHA_PNG}.png",
        f"{TENANT}/uploads/../uploads/chat/{SHA_PNG}.png",
        f"{TENANT}/uploads/chat/{SHA_PNG}.png/..",
        f"{TENANT}//uploads/chat/{SHA_PNG}.png",                # segmen kosong
        f"/{TENANT}/uploads/chat/{SHA_PNG}.png",
        f"{TENANT}\\uploads\\chat\\{SHA_PNG}.png",               # backslash
        f"{TENANT}/uploads/chat/{SHA_PNG}%2epng",               # persen
        f"{TENANT}%2Fuploads/chat/{SHA_PNG}.png",
        f"{TENANT}/uploads/lain/{SHA_PNG}.png",                 # subdir tak sah
        f"{TENANT}/uploads/chat/{SHA_PNG.upper()}.png",         # huruf besar
        f"{TENANT}/uploads/chat/{SHA_PNG[:63]}.png",            # hash pendek
        f"{TENANT}/uploads/chat/{SHA_PNG}.html",                # ext di luar allowlist
        f"{TENANT}/uploads/chat/{SHA_PNG}",                     # tanpa ext
        f"{TENANT}/uploads/chat/nota.png",
        f"{TENANT}/other/2026/09/fe7a49c5_IMG_0701.jpg",        # kunci /api/documents
        "",
    ],
)
@pytest.mark.asyncio
async def test_get_kunci_tak_sah_404_tanpa_storage(chat, k):
    # objek BENAR-BENAR ada di storage untuk tiap kunci, supaya 404 = penolakan
    chat.storage.client.objek[(BUCKET, k)] = b"rahasia"
    with pytest.raises(HTTPException) as e:
        await _get(chat.storage, k)
    assert e.value.status_code == 404
    assert chat.storage.client.get_calls == []


@pytest.mark.parametrize("sub", ["chat", "documents", "forms"])
@pytest.mark.asyncio
async def test_get_kunci_bentuk_lama_404_meski_berkas_ada_di_disk(chat, sub):
    """Bentuk lama <tenant>/<sub>/<sha><ext> -> 404 bersih; disk TIDAK dibaca
    walau berkasnya ada."""
    d = chat.tmp / TENANT / sub
    d.mkdir(parents=True)
    (d / f"{SHA_PNG}.png").write_bytes(ISI_PNG)
    with pytest.raises(HTTPException) as e:
        await _get(chat.storage, f"{TENANT}/{sub}/{SHA_PNG}.png")
    assert e.value.status_code == 404


def test_rute_files_tak_menyentuh_disk():
    teks = SUMBER_UC.read_text(encoding="utf-8")
    i = teks.index("async def get_chat_file(")
    badan = teks[i : teks.index("\nasync def ", i + 10)]
    assert "sajikan_objek_unggahan(" in badan
    for terlarang in ("UPLOAD_BASE_DIR", "FileResponse", "os.path", "open("):
        assert terlarang not in badan, terlarang


# ============== 5. lampiran documents: SEMUA baris -> rute download modul
def test_url_lampiran_local_berkas_chat_ke_rute_download():
    induk, lamp = uuid.uuid4(), uuid.uuid4()
    url_chat = f"/api/v3/chat/files/{TENANT}/forms/{SHA_PNG}.jpg"
    assert (
        lu.url_lampiran_dokumen("expenses", induk, lamp, "local", url_chat)
        == f"/api/expenses/{induk}/attachments/{lamp}/download"
    )
    url_baru = "/api/v3/chat/files/" + kunci("forms")
    assert (
        lu.url_lampiran_dokumen("expenses", induk, lamp, "s3", url_baru)
        == f"/api/expenses/{induk}/attachments/{lamp}/download"
    )


def test_baris_s3_form_terbuka_lewat_rute_download_modul():
    """Baris s3 baru dari form = bentuk yang dilayani stream_lampiran."""
    storage = FakeStorage()
    k = kunci("forms", ext=".jpg")
    storage.client.objek[(BUCKET, k)] = b"jpg"
    resp = lu.stream_lampiran(
        {"storage_type": "s3", "file_path": k, "file_type": "image/jpeg",
         "file_name": "nota-bébé.jpg"},
        storage,
    )
    assert isinstance(resp, StreamingResponse)
    assert storage.client.get_calls == [(BUCKET, k)]


# ============== boto3 sinkron tak boleh memblokir event loop
@pytest.mark.asyncio
async def test_put_dan_get_berjalan_di_threadpool(form, chat):
    utama = threading.get_ident()
    await UP.upload_document_for_form(req(), berkas())
    fm = await UC._store_upload_file(berkas(), TENANT, chat.pool)
    resp = await UC.get_chat_file(req(), fm["storage_key"])
    await baca_stream(resp)
    semua = form.storage.client.utas + chat.storage.client.utas
    assert [op for op, _ in semua] == ["put", "put", "get"]
    assert all(t != utama for _, t in semua), semua
