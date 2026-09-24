"""Unit U2 -- pembaca TERTUNDA unggahan -> MinIO (24 Sep 2026).

Latar (diukur 24 Sep, master cbf6336a): U1 memindahkan unggahan form/chat ke
MinIO, tetapi empat pembaca masih membaca DISK kontainer
(/tmp/milkyhoop_uploads, hilang tiap recreate) sehingga U1 terpaksa menulis
tembolok disk sementara (TEMBOLOK_DISK_PEMBACA_TERTUNDA=True):
  1. file_ref impor rekening koran (utils/file_ref + tool_executor
     _execute_import_bank_statement) dan gerbang workflow rekonsiliasi
     (workflow_engine.check_has_file_or_nofile, os.path.exists);
  2. antrean FIX_MULTIDOC (pending_document_queue menyimpan stored_path,
     _process_one_document open()),
  3. /api/document-intake (document_intake menulis <t>/documents/ di disk,
     document_processor + openai_provider open()/fitz.open(path)).
Kini semuanya membaca BYTES dari MinIO lewat kunci milik tenant konteks, dan
unggah chat = NOL tulisan disk.

Gerbang ini MERAH pada kode lama (dijalankan terhadap deploy/master).
"""
import ast
import base64
import builtins
import hashlib
import io
import os
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

os.environ.setdefault("OPENAI_API_KEY", "sk-boneka-unit-test-tanpa-jaringan")

from app.routers import unified_chat as UC  # noqa: E402
from app.services import document_intake as DI  # noqa: E402
from app.services import document_processor as DP  # noqa: E402
from app.services import storage_service as SS  # noqa: E402
from app.services.unified_agent import workflow_engine as WE  # noqa: E402
from app.services.unified_agent import tool_executor as TE  # noqa: E402

TENANT = "kaos-biru-konveksi"
TENANT_LAIN = "tenant-lain"
BUCKET = "milkyhoop-documents"
APP = Path(__file__).resolve().parents[2] / "app"

ISI_CSV = b"tanggal,keterangan,jumlah\n2026-09-01,setoran,150000\n"
SHA_CSV = hashlib.sha256(ISI_CSV).hexdigest()
REF_CSV = f"chat_upload:{SHA_CSV}.csv"
KUNCI_CSV = f"{TENANT}/uploads/chat/{SHA_CSV}.csv"

ISI_JPG = b"\xff\xd8\xff\xe0bukan-jpeg-sungguhan" * 50
SHA_JPG = hashlib.sha256(ISI_JPG).hexdigest()
KUNCI_JPG = f"{TENANT}/uploads/chat/{SHA_JPG}.jpg"


# ----------------------------------------------------------------- palsu
class ObjekHilang(Exception):
    def __init__(self):
        super().__init__("NoSuchKey")
        self.response = {"Error": {"Code": "NoSuchKey"}}


class StorageMati(Exception):
    def __init__(self):
        super().__init__("EndpointConnectionError")
        self.response = {"Error": {"Code": "EndpointConnectionError"}}


class FakeBody:
    def __init__(self, data):
        self._b = io.BytesIO(data)

    def read(self, n=-1):
        return self._b.read(n)

    def close(self):
        pass


class FakeClient:
    def __init__(self):
        self.objek = {}
        self.panggilan = []  # (op, key, thread id)
        self.mati = False

    def _catat(self, op, key):
        self.panggilan.append((op, key, threading.get_ident()))
        if self.mati:
            raise StorageMati()

    def get_object(self, Bucket, Key):
        self._catat("get", Key)
        if (Bucket, Key) not in self.objek:
            raise ObjekHilang()
        return {"Body": FakeBody(self.objek[(Bucket, Key)])}

    def head_object(self, Bucket, Key):
        self._catat("head", Key)
        if (Bucket, Key) not in self.objek:
            raise ObjekHilang()
        return {"ContentLength": len(self.objek[(Bucket, Key)])}

    def put_object(self, **kw):
        self._catat("put", kw["Key"])
        self.objek[(kw["Bucket"], kw["Key"])] = kw["Body"]
        return {}


class FakeStorage:
    def __init__(self):
        self.client = FakeClient()
        self.config = SimpleNamespace(bucket=BUCKET)

    def taruh(self, kunci, isi):
        self.client.objek[(BUCKET, kunci)] = isi


class PenjagaDisk:
    """Mencatat SETIAP akses disk ke direktori unggahan lama atau tmp_path
    (open/exists/makedirs/replace). Kode baru tak boleh menyentuhnya."""

    def __init__(self, monkeypatch, tmp_path):
        self.akses = []
        self.akar = ("milkyhoop_uploads", str(tmp_path))
        asli_open, asli_exists = builtins.open, os.path.exists
        asli_makedirs, asli_replace = os.makedirs, os.replace

        def _cek(op, p):
            if any(a in str(p) for a in self.akar):
                self.akses.append((op, str(p)))

        def _open(p, *a, **k):
            _cek("open", p)
            return asli_open(p, *a, **k)

        def _exists(p):
            _cek("exists", p)
            return asli_exists(p)

        def _makedirs(p, *a, **k):
            _cek("makedirs", p)
            return asli_makedirs(p, *a, **k)

        def _replace(a_, b_, *a, **k):
            _cek("replace", b_)
            return asli_replace(a_, b_, *a, **k)

        monkeypatch.setattr(builtins, "open", _open)
        monkeypatch.setattr(os.path, "exists", _exists)
        monkeypatch.setattr(os, "makedirs", _makedirs)
        monkeypatch.setattr(os, "replace", _replace)


@pytest.fixture
def st(monkeypatch, tmp_path):
    storage = FakeStorage()
    monkeypatch.setattr(SS, "get_storage_service", lambda: storage)
    # modul yang mengikat get_storage_service saat impor: tambal di modulnya
    for mod in (UC, DI, DP):
        monkeypatch.setattr(mod, "get_storage_service", lambda: storage, raising=False)
    # kode lama membaca disk di sini -> arahkan ke tmp kosong
    for mod in ("app.utils.file_ref", "utils.file_ref"):
        try:
            m = __import__(mod, fromlist=["x"])
            monkeypatch.setattr(m, "UPLOAD_BASE_DIR", str(tmp_path), raising=False)
        except Exception:  # noqa: BLE001
            pass
    storage.tmp = tmp_path
    storage.disk = PenjagaDisk(monkeypatch, tmp_path)
    return storage


def utas_non_utama(storage):
    utama = threading.get_ident()
    return all(t != utama for _, _, t in storage.client.panggilan)


# ============================ 1. impor rekening koran lewat file_ref -> MinIO
class FakeResp:
    status_code = 200
    text = "{}"

    def json(self):
        return {"data": {"lines_imported": 1, "total_debits": 0, "total_credits": 150000}}


def _pasang_httpx(monkeypatch):
    kirim = []

    class FakeAsyncClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, **kw):
            kirim.append((url, kw))
            return FakeResp()

    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)
    return kirim


def _executor():
    return SimpleNamespace(
        context=SimpleNamespace(tenant_id=TENANT),
        _build_headers=lambda: {"Authorization": "Bearer x", "Content-Type": "application/json"},
    )


PETA_KOLOM = {"date_column": "tanggal", "description_column": "keterangan",
              "amount_column": "jumlah"}


@pytest.mark.asyncio
async def test_impor_file_ref_bytes_dari_storage_tanpa_disk(st, monkeypatch):
    st.taruh(KUNCI_CSV, ISI_CSV)
    kirim = _pasang_httpx(monkeypatch)
    out = await TE.ToolExecutor._execute_import_bank_statement(
        _executor(),
        {"session_id": "sesi-1", "file_ref": REF_CSV, "config": dict(PETA_KOLOM)},
    )
    assert out["success"] is True, out
    assert len(kirim) == 1
    nama, isi, _ = kirim[0][1]["files"]["file"]
    assert isi == ISI_CSV
    assert nama == f"{SHA_CSV}.csv" and "/" not in nama  # tanpa path server
    assert [(op, k) for op, k, _ in st.client.panggilan] == [("get", KUNCI_CSV)]
    assert utas_non_utama(st)
    assert st.disk.akses == []


@pytest.mark.asyncio
async def test_impor_objek_hilang_minta_unggah_ulang(st, monkeypatch):
    kirim = _pasang_httpx(monkeypatch)
    # berkas ADA di disk lama -- tak boleh dipakai
    d = st.tmp / TENANT / "chat"
    d.mkdir(parents=True)
    (d / f"{SHA_CSV}.csv").write_bytes(ISI_CSV)
    st.disk.akses.clear()
    out = await TE.ToolExecutor._execute_import_bank_statement(
        _executor(), {"session_id": "sesi-1", "file_ref": REF_CSV, "config": dict(PETA_KOLOM)}
    )
    assert out["success"] is False
    assert out["error"]["code"] == "INVALID_FILE_REF"
    assert "upload ulang" in out["error"]["message"]
    assert kirim == [] and st.disk.akses == []


@pytest.mark.parametrize(
    "ref",
    [
        f"chat_upload:{SHA_CSV}",                     # tanpa ext
        f"chat_upload:../{SHA_CSV}.csv",
        f"chat_upload:{SHA_CSV[:40]}.csv",             # hash pendek
        f"chat_upload:{SHA_CSV}.html",                 # ext di luar allowlist
        f"form_upload:{SHA_CSV}.csv",
        f"{TENANT_LAIN}/uploads/chat/{SHA_CSV}.csv",   # kunci mentah tenant lain
        f"chat_upload:{TENANT_LAIN}/uploads/chat/{SHA_CSV}.csv",
    ],
)
@pytest.mark.asyncio
async def test_impor_file_ref_tak_sah_ditolak_tanpa_storage(st, monkeypatch, ref):
    st.taruh(KUNCI_CSV, ISI_CSV)
    st.taruh(f"{TENANT_LAIN}/uploads/chat/{SHA_CSV}.csv", b"rahasia")
    kirim = _pasang_httpx(monkeypatch)
    out = await TE.ToolExecutor._execute_import_bank_statement(
        _executor(), {"session_id": "sesi-1", "file_ref": ref, "config": dict(PETA_KOLOM)}
    )
    assert out["success"] is False and kirim == []
    assert st.client.panggilan == []


@pytest.mark.parametrize(
    "ref",
    [f"chat_upload:{SHA_CSV[:40]}.csv", f"chat_upload:{SHA_CSV}.html",
     f"chat_upload:{SHA_CSV}.CSV", "chat_upload:", f"form_upload:{SHA_CSV}.csv", None],
)
def test_kunci_file_ref_menolak_bentuk_salah(ref):
    """Kontrak fungsi murni (lapis pertama; ambil_objek_unggahan = lapis
    kedua): bentuk salah -> None, bukan kunci yang 'nanti ditolak storage'."""
    from app.utils.file_ref import kunci_file_ref

    assert kunci_file_ref(ref, TENANT) is None


def test_kunci_file_ref_memetakan_ke_tenant_konteks():
    from app.utils.file_ref import kunci_file_ref

    assert kunci_file_ref(REF_CSV, TENANT) == KUNCI_CSV
    assert kunci_file_ref(REF_CSV, TENANT_LAIN) == f"{TENANT_LAIN}/uploads/chat/{SHA_CSV}.csv"


@pytest.mark.asyncio
async def test_impor_file_path_mentah_tak_lagi_diterima(st, monkeypatch, tmp_path):
    p = tmp_path / "mutasi.csv"
    p.write_bytes(ISI_CSV)
    st.disk.akses.clear()
    kirim = _pasang_httpx(monkeypatch)
    out = await TE.ToolExecutor._execute_import_bank_statement(
        _executor(), {"session_id": "sesi-1", "file_path": str(p), "config": dict(PETA_KOLOM)}
    )
    assert out["success"] is False and kirim == []
    assert st.disk.akses == []


@pytest.mark.asyncio
async def test_impor_storage_mati_galat_jelas_bukan_crash(st, monkeypatch):
    st.taruh(KUNCI_CSV, ISI_CSV)
    st.client.mati = True
    kirim = _pasang_httpx(monkeypatch)
    out = await TE.ToolExecutor._execute_import_bank_statement(
        _executor(), {"session_id": "sesi-1", "file_ref": REF_CSV, "config": dict(PETA_KOLOM)}
    )
    assert out["success"] is False and kirim == []
    assert "coba lagi" in out["error"]["message"]


# ================================ 2. gerbang workflow check_has_file_or_nofile
def _wctx(ref, tenant=TENANT):
    return SimpleNamespace(data={"file_ref": ref}, tenant_id=tenant)


@pytest.mark.asyncio
async def test_gerbang_objek_ada_lolos_tanpa_disk(st):
    st.taruh(KUNCI_CSV, ISI_CSV)
    ctx = _wctx(REF_CSV)
    assert await WE.check_has_file_or_nofile(ctx, {}) == (True, "")
    assert ctx.data["file_ref"] == REF_CSV
    assert [(op, k) for op, k, _ in st.client.panggilan] == [("head", KUNCI_CSV)]
    assert utas_non_utama(st)
    assert st.disk.akses == []


@pytest.mark.asyncio
async def test_gerbang_objek_hilang_minta_unggah_ulang(st):
    d = st.tmp / TENANT / "chat"  # ada di disk lama -- tak boleh jadi bukti
    d.mkdir(parents=True)
    (d / f"{SHA_CSV}.csv").write_bytes(ISI_CSV)
    st.disk.akses.clear()
    ctx = _wctx(REF_CSV)
    ok, pesan = await WE.check_has_file_or_nofile(ctx, {})
    assert ok is False and "upload ulang" in pesan
    assert "file_ref" not in ctx.data
    assert st.disk.akses == []


@pytest.mark.parametrize(
    "ref", [f"chat_upload:{SHA_CSV}.html", f"chat_upload:{SHA_CSV[:10]}.csv", "sembarang"]
)
@pytest.mark.asyncio
async def test_gerbang_bentuk_salah_ditolak_tanpa_storage(st, ref):
    ctx = _wctx(ref)
    ok, pesan = await WE.check_has_file_or_nofile(ctx, {})
    assert ok is False and "upload ulang" in pesan
    assert st.client.panggilan == []


@pytest.mark.asyncio
async def test_gerbang_tenant_konteks_bukan_tenant_objek(st):
    """Objek hanya ada di tenant lain -> konteks TENANT tak boleh lolos."""
    st.taruh(f"{TENANT_LAIN}/uploads/chat/{SHA_CSV}.csv", ISI_CSV)
    ok, _ = await WE.check_has_file_or_nofile(_wctx(REF_CSV), {})
    assert ok is False
    assert [k for _, k, _ in st.client.panggilan] == [KUNCI_CSV]


@pytest.mark.asyncio
async def test_gerbang_storage_mati_file_ref_dipertahankan(st):
    st.taruh(KUNCI_CSV, ISI_CSV)
    st.client.mati = True
    ctx = _wctx(REF_CSV)
    ok, pesan = await WE.check_has_file_or_nofile(ctx, {})
    assert ok is False and "coba lagi" in pesan
    assert ctx.data["file_ref"] == REF_CSV


# ================================= 3. antrean FIX_MULTIDOC: kunci, bukan path
class OcrTertangkap(Exception):
    pass


def _pasang_ocr(monkeypatch):
    tangkap = []

    class FakeCompletions:
        async def create(self, **kw):
            tangkap.append(kw)
            raise OcrTertangkap()

    class FakeOpenAI:
        def __init__(self, *a, **k):
            self.chat = SimpleNamespace(completions=FakeCompletions())

    import openai

    monkeypatch.setattr(openai, "AsyncOpenAI", FakeOpenAI)

    async def _pool():
        return None

    from app.services.unified_agent import db_utils

    monkeypatch.setattr(db_utils, "get_session_db_pool", _pool)
    return tangkap


def _url_gambar(kw):
    return kw["messages"][0]["content"][1]["image_url"]["url"]


CTX = {"tenant_id": TENANT, "user_id": "22222222-2222-2222-2222-222222222222"}


@pytest.mark.asyncio
async def test_antrean_proses_membaca_bytes_dari_storage(st, monkeypatch):
    st.taruh(KUNCI_JPG, ISI_JPG)
    tangkap = _pasang_ocr(monkeypatch)
    fm = {"storage_key": KUNCI_JPG, "content_type": "image/jpeg",
          "extension": ".jpg", "filename": "nota2.jpg", "file_hash": SHA_JPG}
    out = await UC._process_one_document(fm, ctx=CTX, session_id=None)
    assert out is None  # OCR palsu meledak sesudah menangkap -> None
    assert len(tangkap) == 1
    # PIL gagal membuka isi palsu -> byte mentah dikirim apa adanya
    assert _url_gambar(tangkap[0]).endswith(base64.b64encode(ISI_JPG).decode())
    assert [(op, k) for op, k, _ in st.client.panggilan] == [("get", KUNCI_JPG)]
    assert utas_non_utama(st)
    assert st.disk.akses == []


@pytest.mark.asyncio
async def test_antrean_entri_bentuk_path_lama_dilewati(st, monkeypatch):
    """Entri lama {stored_path} -> tak tersedia (catatan lewati, sama seperti
    berkas tak terbaca), walau berkasnya MASIH ada di disk."""
    d = st.tmp / TENANT / "chat"
    d.mkdir(parents=True)
    p = d / f"{SHA_JPG}.jpg"
    p.write_bytes(ISI_JPG)
    st.disk.akses.clear()
    tangkap = _pasang_ocr(monkeypatch)
    fm = {"stored_path": str(p), "content_type": "image/jpeg",
          "extension": ".jpg", "filename": "nota2.jpg", "file_hash": SHA_JPG}
    assert await UC._process_one_document(fm, ctx=CTX, session_id=None) is None
    assert tangkap == [] and st.disk.akses == []


@pytest.mark.parametrize(
    "k",
    [f"{TENANT_LAIN}/uploads/chat/{SHA_JPG}.jpg", f"{TENANT}/chat/{SHA_JPG}.jpg",
     f"{TENANT}/uploads/chat/../chat/{SHA_JPG}.jpg"],
)
@pytest.mark.asyncio
async def test_antrean_kunci_tak_sah_ditolak_tanpa_storage(st, monkeypatch, k):
    st.taruh(k, ISI_JPG)
    tangkap = _pasang_ocr(monkeypatch)
    fm = {"storage_key": k, "content_type": "image/jpeg", "filename": "x.jpg"}
    assert await UC._process_one_document(fm, ctx=CTX, session_id=None) is None
    assert tangkap == [] and st.client.panggilan == []


@pytest.mark.asyncio
async def test_antrean_objek_hilang_dilewati(st, monkeypatch):
    tangkap = _pasang_ocr(monkeypatch)
    fm = {"storage_key": KUNCI_JPG, "content_type": "image/jpeg", "filename": "x.jpg"}
    assert await UC._process_one_document(fm, ctx=CTX, session_id=None) is None
    assert tangkap == []


def _badan_fungsi(teks, kepala):
    i = teks.index(kepala)
    j = teks.index("\nasync def ", i + len(kepala))
    return teks[i:j]


def test_antrean_enqueue_menyimpan_kunci_bukan_path():
    """Entri pending_document_queue dibangun dari storage_key file_meta; tak
    ada lagi stored_path di mana pun di unified_chat."""
    teks = (APP / "routers/unified_chat.py").read_text(encoding="utf-8")
    i = teks.index('_md_dc["pending_document_queue"] = _remaining')
    blok = teks[teks.rindex("_remaining = [", 0, i):i]
    assert '"storage_key": _q.get("storage_key")' in blok
    assert "stored_path" not in blok
    kode = ast.unparse(ast.parse(teks))  # tanpa komentar
    assert "stored_path" not in kode.replace("TANPA stored_path", "")


# ========================== 4. document-intake: tulis & baca lewat MinIO
class FakeIntakeConn:
    def __init__(self):
        self.rows = []

    async def fetchrow(self, sql, *args):
        if sql.strip().startswith("INSERT INTO uploaded_documents"):
            row = {"id": args[0], "tenant_id": args[1], "file_path": args[5],
                   "file_hash": args[6], "batch_id": args[2]}
            self.rows.append(row)
            return row
        return None  # tak ada dedup

    async def execute(self, sql, *args):
        return None


@pytest.mark.asyncio
async def test_intake_simpan_ke_minio_tanpa_disk(st):
    from starlette.datastructures import Headers, UploadFile
    DocumentIntakeService = DI.DocumentIntakeService

    isi = b"%PDF-1.4 faktur"
    sha = hashlib.sha256(isi).hexdigest()
    conn = FakeIntakeConn()
    f = UploadFile(file=io.BytesIO(isi), filename="faktur.pdf",
                   headers=Headers({"content-type": "application/pdf"}))
    await DocumentIntakeService(pool=None)._store_and_insert(
        conn, TENANT, "u", "b", f, isi, sha, ".pdf"
    )
    k = f"{TENANT}/uploads/documents/{sha}.pdf"
    assert st.client.objek[(BUCKET, k)] == isi
    assert conn.rows[0]["file_path"] == k
    assert utas_non_utama(st)
    assert st.disk.akses == []


class FakeProvider:
    def __init__(self):
        self.isi = []

    async def extract(self, isi, mime_type, tier, prompt):
        self.isi.append(isi)
        raise OcrTertangkap()


def _prosesor(monkeypatch):
    p = DP.DocumentProcessor(pool=None)
    prov = FakeProvider()
    p._ocr_provider = prov
    status = []

    async def _upd(doc_id, tenant_id, st_, **kw):
        status.append((st_, kw.get("detail")))

    async def _gagal(*a, **k):
        status.append(("batch_failed", None))

    monkeypatch.setattr(p, "_update_status", _upd)
    monkeypatch.setattr(p, "_update_batch_failed", _gagal)
    return p, prov, status


def _doc(kunci, mime="image/jpeg"):
    return {"id": "d1", "tenant_id": TENANT, "file_path": kunci, "mime_type": mime,
            "retry_count": 0, "max_retries": 3, "batch_id": "b1",
            "original_filename": "x"}


@pytest.mark.asyncio
async def test_prosesor_ocr_menerima_bytes_dari_storage(st, monkeypatch):
    k = f"{TENANT}/uploads/documents/{SHA_JPG}.jpg"
    st.taruh(k, ISI_JPG)
    p, prov, status = _prosesor(monkeypatch)
    out = await p.process_single_document(_doc(k))
    assert prov.isi == [ISI_JPG]
    assert out["status"] == "retry"  # OCR palsu meledak -> jalur retry biasa
    assert [(op, kk) for op, kk, _ in st.client.panggilan] == [("get", k)]
    assert st.disk.akses == []


@pytest.mark.parametrize(
    "k",
    [
        f"/tmp/milkyhoop_uploads/{TENANT}/documents/{SHA_JPG}.jpg",  # baris lama
        f"{TENANT_LAIN}/uploads/documents/{SHA_JPG}.jpg",
        f"{TENANT}/uploads/documents/{SHA_JPG}.jpg",  # objek tak ada (tak ditaruh)
    ],
)
@pytest.mark.asyncio
async def test_prosesor_berkas_tak_tersedia_gagal_final_unggah_ulang(st, monkeypatch, k):
    if k.startswith(TENANT_LAIN):
        st.taruh(k, b"rahasia")
    p, prov, status = _prosesor(monkeypatch)
    out = await p.process_single_document(_doc(k))
    assert out["status"] == "extraction_failed"
    assert prov.isi == []
    assert any(s == "extraction_failed" and "unggah ulang" in (d or "") for s, d in status)
    if not k.startswith(f"{TENANT}/uploads/"):
        assert st.client.panggilan == []  # kunci tak sah -> storage tak disentuh
    assert st.disk.akses == []


@pytest.mark.asyncio
async def test_openai_provider_bytes_gambar_dan_pdf_tanpa_disk(st):
    import fitz
    from app.services.ocr_providers.openai_provider import OpenAIOCRProvider

    prov = OpenAIOCRProvider(api_key="sk-boneka")
    b64, mime = await prov._prepare_image(ISI_JPG, "image/jpeg")
    assert base64.b64decode(b64) == ISI_JPG and mime == "image/jpeg"

    doc = fitz.open()
    doc.new_page(width=100, height=100)
    pdf = doc.tobytes()
    doc.close()
    b64, mime = await prov._prepare_image(pdf, "application/pdf")
    assert mime == "image/png" and base64.b64decode(b64).startswith(b"\x89PNG")
    assert st.disk.akses == []


# ============ 5. kontrak: tak ada pembaca/penulis disk unggahan tersisa di app
def _pelanggaran_disk(teks):
    pohon = ast.parse(teks)
    docstring = set()
    for n in ast.walk(pohon):
        if isinstance(n, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if n.body and isinstance(n.body[0], ast.Expr) and isinstance(
                getattr(n.body[0], "value", None), ast.Constant
            ):
                docstring.add(id(n.body[0].value))
    salah = []
    for n in ast.walk(pohon):
        if isinstance(n, ast.Name) and n.id in ("UPLOAD_BASE_DIR", "TEMBOLOK_DISK_PEMBACA_TERTUNDA"):
            salah.append(n.id)
        elif isinstance(n, ast.Attribute) and n.attr in ("UPLOAD_BASE_DIR",):
            salah.append(n.attr)
        elif (isinstance(n, ast.Constant) and isinstance(n.value, str)
              and id(n) not in docstring
              and ("milkyhoop_uploads" in n.value or n.value == "DOCUMENT_UPLOAD_DIR")):
            salah.append(n.value)
    return salah


def test_kontrak_tak_ada_disk_unggahan_di_app():
    temuan = {}
    for p in sorted(APP.rglob("*.py")):
        s = _pelanggaran_disk(p.read_text(encoding="utf-8"))
        if s:
            temuan[str(p.relative_to(APP))] = s
    assert temuan == {}


def test_kontrak_pendeteksi_bisa_merah():
    """Kontrol: pendeteksi MENANGKAP bentuk lama (bukan pemindai buta)."""
    assert _pelanggaran_disk('UPLOAD_BASE_DIR = "/tmp/milkyhoop_uploads"\n')
    assert _pelanggaran_disk('import os\nx = os.path.join("/tmp/milkyhoop_uploads", t)\n')
    assert _pelanggaran_disk('import os\nx = os.getenv("DOCUMENT_UPLOAD_DIR", "/x")\n')
    assert _pelanggaran_disk("if TEMBOLOK_DISK_PEMBACA_TERTUNDA:\n    pass\n")
    assert not _pelanggaran_disk('"""dulu /tmp/milkyhoop_uploads"""\n')


def test_upload_chat_file_to_pipeline_dihapus():
    from app.services import chat_document_bridge as B

    assert not hasattr(B, "upload_chat_file_to_pipeline")
    assert hasattr(B, "process_document_sync")  # kontrol: modulnya tetap ada
