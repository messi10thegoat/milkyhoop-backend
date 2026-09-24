"""Unit 1b — lampiran faktur penjualan, faktur pembelian, dan beban diunduh
LEWAT GATEWAY (lanjutan Unit 1a; helper `app/utils/lampiran_unduh.py`).

Latar (diukur 24 Sep 2026): MinIO hanya 127.0.0.1:9000 -> URL presign
(`storage.generate_signed_url`, host MINIO_PUBLIC_ENDPOINT :9000) mati dari
luar. Tambahan per modul:
  - sales-invoices: daftar/unggah = presign; rute download yang sudah ada
    meneruskan nama berkas MENTAH ke Content-Disposition dan menjawab 500
    untuk objek hilang.
  - bills: daftar punya DUA sumber (bill_attachments + documents via
    document_attachments entity_type='bill'), keduanya presign; rute download
    hanya membaca bill_attachments -> id sumber kedua = 404. Detail
    (bills_service) JATUH ke `file_path` mentah bila presign gagal = kunci
    storage bocor.
  - expenses: url = `documents.file_url` mentah (NULL untuk baris s3), dan
    rute download belum ada.

Handler dipanggil PENUH dengan pool/konektor/storage palsu (pola 1a) supaya
penyambungannya ikut terjaga, plus kontrak AST untuk jalur yang terlalu
berat dipanggil utuh (detail bill, buat-beban).
"""
import ast
import io
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.responses import StreamingResponse
from starlette.datastructures import Headers, UploadFile

from app.routers import bills as bl
from app.routers import expenses as ex
from app.routers import sales_invoices as si
from app.services import bills_service as bsvc
from app.middleware import permission_middleware as pm
from app.utils import lampiran_unduh as lu

TENANT = "kaos-biru-konveksi"
TENANT_LAIN = "tenant-lain"
USER_ID = "22222222-2222-2222-2222-222222222222"
INDUK = uuid.UUID("11111111-1111-1111-1111-111111111111")
LAMPIRAN = uuid.UUID("33333333-3333-3333-3333-333333333333")
LAMPIRAN_DOK = uuid.UUID("44444444-4444-4444-4444-444444444444")
KUNCI = "kaos-biru-konveksi/other/2026/09/fe7a49c5_IMG_0701.jpg"
PRESIGN = (
    "http://159.89.202.160:9000/milkyhoop-documents/"
    + KUNCI
    + "?X-Amz-Signature=abc"
)
URL_CHAT = "/api/v3/chat/files/kaos-biru-konveksi/forms/" + "a" * 64 + ".jpg"
ISI = b"\x89PNG isi-berkas-lampiran" * 5000  # > 64 KiB -> lebih dari satu potong
WAKTU = datetime(2026, 9, 24, tzinfo=timezone.utc)


# ----------------------------------------------------------------- palsu
class FakeConn:
    def __init__(self, on_fetchrow=None, on_fetch=None):
        self.calls = []
        self._fr = on_fetchrow or (lambda sql, args: None)
        self._f = on_fetch or (lambda sql, args: [])

    async def execute(self, sql, *args):
        self.calls.append(("execute", sql, args))

    async def fetchrow(self, sql, *args):
        self.calls.append(("fetchrow", sql, args))
        return self._fr(sql, args)

    async def fetch(self, sql, *args):
        self.calls.append(("fetch", sql, args))
        return self._f(sql, args)

    async def fetchval(self, sql, *args):
        self.calls.append(("fetchval", sql, args))
        r = self._fr(sql, args)
        return r["id"] if r else None

    def transaction(self):
        return _Ctx(None)


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


class FakeBody:
    def __init__(self, data):
        self._b = io.BytesIO(data)
        self.closed = False

    def read(self, n=-1):
        return self._b.read(n)

    def close(self):
        self.closed = True


class ObjekHilang(Exception):
    """Meniru botocore ClientError NoSuchKey."""

    def __init__(self):
        super().__init__("NoSuchKey")
        self.response = {"Error": {"Code": "NoSuchKey"}}


class FakeClient:
    def __init__(self, data=ISI, hilang=False):
        self.data = data
        self.hilang = hilang
        self.get_calls = []

    def get_object(self, Bucket, Key):
        self.get_calls.append((Bucket, Key))
        if self.hilang:
            raise ObjekHilang()
        return {"Body": FakeBody(self.data)}


class FakeStorage:
    def __init__(self, hilang=False):
        self.client = FakeClient(hilang=hilang)
        self.config = SimpleNamespace(bucket="milkyhoop-documents", url_expiry=3600)
        self.presign_calls = []

    async def generate_signed_url(self, file_path, expires_in=None):
        self.presign_calls.append(file_path)
        return PRESIGN

    async def upload_file(self, file, tenant_id, category):
        return SimpleNamespace(file_path=KUNCI, url=PRESIGN)


def req(tenant=TENANT):
    return SimpleNamespace(
        state=SimpleNamespace(user={"tenant_id": tenant, "user_id": USER_ID})
    )


def pasang(monkeypatch, mod, conn, hilang=False):
    storage = FakeStorage(hilang=hilang)

    async def _pool():
        return FakePool(conn)

    monkeypatch.setattr(mod, "get_pool", _pool)
    monkeypatch.setattr(mod, "get_storage_service", lambda: storage)
    return storage


def baris_lama(**kw):
    """Baris sales_invoice_attachments / bill_attachments (medan mentah)."""
    r = {
        "id": LAMPIRAN,
        "filename": "nota.jpg",
        "file_path": KUNCI,
        "file_size": 1234,
        "mime_type": "image/jpeg",
        "uploaded_at": WAKTU,
        "uploaded_by": None,
        "uploaded_by_name": "Pemilik",
    }
    r.update(kw)
    return r


def baris_dok(**kw):
    """Baris documents x document_attachments -- membawa file_url/file_path
    mentah supaya kembalinya kode ke url mentah langsung terlihat."""
    r = {
        "id": LAMPIRAN_DOK,
        "file_name": "bukti transfer.jpg",
        "file_size": 1234,
        "file_type": "image/jpeg",
        "mime_type": "image/jpeg",
        "width": None,
        "height": None,
        "file_path": KUNCI,
        "file_url": PRESIGN,
        "storage_type": "s3",
        "uploaded_at": WAKTU,
        "display_order": 0,
    }
    r.update(kw)
    return r


def url_sah(url, modul, induk=INDUK, lampiran=LAMPIRAN):
    assert url == f"/api/{modul}/{induk}/attachments/{lampiran}/download", url
    assert ":9000" not in url
    assert "http" not in url
    assert KUNCI not in url and "kaos-biru-konveksi/" not in url


def upload(nama="nota.png", tipe="image/png"):
    return UploadFile(
        file=io.BytesIO(b"\x89PNG kecil"),
        filename=nama,
        headers=Headers({"content-type": tipe}),
    )


# ================================================== 0. helper url dokumen
def test_url_lampiran_dokumen_s3_ke_rute_download():
    url_sah(
        lu.url_lampiran_dokumen("expenses", INDUK, LAMPIRAN, "s3", None),
        "expenses",
    )
    # s3 dengan file_url apa pun (termasuk bentuk berkas-chat) tetap rute modul
    url_sah(
        lu.url_lampiran_dokumen("expenses", INDUK, LAMPIRAN, "s3", URL_CHAT),
        "expenses",
    )


# DIBALIK di Unit U1 (unggahan-persisten): cabang sementara "baris local
# berkas-chat -> path chat" dihapus; baris local juga ke rute download modul
# (yang menjawab 404 bersih karena berkas lokalnya sudah hilang).
def test_url_lampiran_dokumen_local_berkas_chat_ke_rute_download():
    url_sah(
        lu.url_lampiran_dokumen("expenses", INDUK, LAMPIRAN, "local", URL_CHAT),
        "expenses",
    )
    assert not hasattr(lu, "_URL_BERKAS_CHAT")


@pytest.mark.parametrize(
    "file_url",
    [
        None,
        "",
        PRESIGN,
        KUNCI,
        "http://evil.example/api/v3/chat/files/kaos-biru-konveksi/forms/" + "a" * 64,
        "/api/v3/chat/files/../../etc/passwd",
        "/api/v3/chat/files/kaos-biru-konveksi/lain/" + "a" * 64 + ".jpg",
    ],
)
def test_url_lampiran_dokumen_local_selain_berkas_chat_ke_rute(file_url):
    url_sah(
        lu.url_lampiran_dokumen("expenses", INDUK, LAMPIRAN, "local", file_url),
        "expenses",
    )


# ================================================== 1a. sales-invoices url
@pytest.mark.asyncio
async def test_si_daftar_url_relatif_bukan_presign(monkeypatch):
    conn = FakeConn(
        on_fetchrow=lambda s, a: {"id": INDUK},
        on_fetch=lambda s, a: [baris_lama()],
    )
    storage = pasang(monkeypatch, si, conn)
    out = await si.list_invoice_attachments(req(), INDUK)
    assert len(out["attachments"]) == 1
    att = out["attachments"][0]
    url_sah(att["url"], "sales-invoices")
    assert storage.presign_calls == []
    # medan lain dipertahankan
    assert set(att) == {
        "id", "filename", "url", "size", "mime_type", "uploaded_at",
        "uploaded_by_name",
    }


@pytest.mark.asyncio
async def test_si_unggah_url_relatif_bukan_presign(monkeypatch):
    conn = FakeConn(on_fetchrow=lambda s, a: {"id": INDUK})
    pasang(monkeypatch, si, conn)
    out = await si.upload_invoice_attachment(req(), INDUK, upload())
    url_sah(out["data"]["url"], "sales-invoices", lampiran=out["data"]["id"])


# ================================================== 1b. bills url
def _bills_fetch(doc_rows):
    def f(sql, args):
        if "document_attachments" in sql:
            return doc_rows
        return [baris_lama()]

    return f


@pytest.mark.asyncio
async def test_bills_daftar_dua_sumber_url_relatif(monkeypatch):
    doc_rows = [
        baris_dok(),
        baris_dok(
            id=uuid.UUID("55555555-5555-5555-5555-555555555555"),
            storage_type="local",
            file_url=URL_CHAT,
        ),
        baris_dok(
            id=uuid.UUID("66666666-6666-6666-6666-666666666666"),
            storage_type="local",
            file_url=None,
        ),
    ]
    conn = FakeConn(
        on_fetchrow=lambda s, a: {"id": INDUK},
        on_fetch=_bills_fetch(doc_rows),
    )
    storage = pasang(monkeypatch, bl, conn)
    out = await bl.list_bill_attachments(req(), INDUK)
    atts = out["attachments"]
    assert len(atts) == 4
    url_sah(atts[0]["url"], "bills")  # bill_attachments
    url_sah(atts[1]["url"], "bills", lampiran=LAMPIRAN_DOK)  # documents s3
    # local berkas-chat: rute download juga (Unit U1 membalik cabang sementara)
    url_sah(atts[2]["url"], "bills", lampiran="55555555-5555-5555-5555-555555555555")
    url_sah(atts[3]["url"], "bills", lampiran="66666666-6666-6666-6666-666666666666")
    assert storage.presign_calls == []
    for a in atts:
        assert "/api/documents/" not in (a["url"] or "")
    # sumber kedua tetap dipagari tenant + faktur ini
    sql, args = [(c[1], c[2]) for c in conn.calls if c[0] == "fetch"][1]
    assert "da.entity_type = 'bill'" in sql
    assert args == (TENANT, INDUK)


@pytest.mark.asyncio
async def test_bills_unggah_url_relatif_bukan_presign(monkeypatch):
    conn = FakeConn(on_fetchrow=lambda s, a: {"id": INDUK})
    pasang(monkeypatch, bl, conn)
    out = await bl.upload_attachment(req(), INDUK, upload())
    url_sah(out["data"]["url"], "bills", lampiran=out["data"]["id"])


def test_bills_service_detail_url_relatif_tanpa_file_path():
    rows = [baris_lama(), baris_lama(id=LAMPIRAN_DOK, file_path=None)]
    out = bsvc.BillsService._map_attachments_with_urls(None, INDUK, rows)
    url_sah(out[0]["url"], "bills")
    url_sah(out[1]["url"], "bills", lampiran=LAMPIRAN_DOK)
    for a in out:
        assert KUNCI not in repr(a)
        assert set(a) == {"id", "filename", "url", "size", "mime_type", "uploaded_at"}


def test_bills_service_get_bill_menyambung_ke_pemeta():
    """Kontrak AST: get_bill membentuk lampiran lewat
    _map_attachments_with_urls(bill_id, ...) dan pemetanya tak lagi memanggil
    presign atau menjadikan file_path url."""
    src = Path(bsvc.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    fn = next(
        n for n in ast.walk(tree)
        if isinstance(n, ast.AsyncFunctionDef) and n.name == "get_bill"
    )
    panggil = [
        n for n in ast.walk(fn)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and n.func.attr == "_map_attachments_with_urls"
    ]
    assert len(panggil) == 1
    assert ast.unparse(panggil[0].args[0]) == "bill_id"
    peta = next(
        n for n in ast.walk(tree)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        and n.name == "_map_attachments_with_urls"
    )
    # badan fungsi TANPA docstring (docstring boleh menyebut riwayatnya)
    badan = [
        st for st in peta.body
        if not (isinstance(st, ast.Expr) and isinstance(st.value, ast.Constant))
    ]
    teks = "\n".join(ast.unparse(st) for st in badan)
    assert "generate_signed_url" not in teks
    assert "file_path" not in teks


# ================================================== 1c. expenses url
@pytest.mark.asyncio
async def test_exp_daftar_url_relatif_bukan_file_url(monkeypatch):
    rows = [
        baris_dok(file_url=None),  # s3: file_url NULL (keadaan prod)
        baris_dok(
            id=uuid.UUID("55555555-5555-5555-5555-555555555555"),
            storage_type="local",
            file_url=URL_CHAT,
        ),
    ]
    for r in rows:  # bentuk SELECT daftar beban (tanpa file_path)
        r.pop("file_path")
        r.pop("file_type")
    conn = FakeConn(
        on_fetchrow=lambda s, a: {"id": INDUK},
        on_fetch=lambda s, a: rows,
    )
    pasang(monkeypatch, ex, conn)
    out = await ex.list_expense_attachments(req(), INDUK)
    data = out["data"]
    url_sah(data[0]["url"], "expenses", lampiran=LAMPIRAN_DOK)
    url_sah(data[1]["url"], "expenses", lampiran="55555555-5555-5555-5555-555555555555")
    for a in data:
        assert a["thumbnail_url"] is None
        # medan respons lama dipertahankan; tak ada medan mentah baru
        assert set(a) == {
            "id", "file_name", "file_size", "mime_type", "width", "height",
            "url", "thumbnail_url", "uploaded_at", "display_order",
        }
    sql = [c[1] for c in conn.calls if c[0] == "fetch"][0]
    assert "file_url as url" not in sql
    assert "thumbnail_path" not in sql


def test_exp_buat_beban_menyambung_ke_pembentuk_lampiran():
    """Kontrak AST: respons POST /api/expenses membentuk lampiran lewat
    _exp_lampiran_ke_respons(attachments, expense_id), bukan dict(a) mentah."""
    src = Path(ex.__file__).read_text(encoding="utf-8")
    fn = next(
        n for n in ast.walk(ast.parse(src))
        if isinstance(n, ast.AsyncFunctionDef) and n.name == "create_expense"
    )
    panggil = [
        n for n in ast.walk(fn)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Name)
        and n.func.id == "_exp_lampiran_ke_respons"
    ]
    assert [ast.unparse(a) for c in panggil for a in c.args] == [
        "attachments", "expense_id"
    ]
    teks = ast.unparse(fn)
    assert "file_url as url" not in teks
    assert "thumbnail_path" not in teks


# ================================================== 2. rute download
# (id, modul, fungsi, lampiran, sumber) -- sumber menentukan SQL mana yang
# "memuat" baris di DB palsu.
RUTE = [
    ("si", si, "download_invoice_attachment", LAMPIRAN, "sales_invoice_attachments"),
    ("bills-lama", bl, "download_bill_attachment", LAMPIRAN, "bill_attachments"),
    ("bills-dok", bl, "download_bill_attachment", LAMPIRAN_DOK, "document_attachments"),
    ("expenses", ex, "download_expense_attachment", LAMPIRAN_DOK, "document_attachments"),
]
IDS = [r[0] for r in RUTE]


def db_satu_lampiran(sumber, lampiran, storage_type="s3", pemilik=TENANT, file_path=KUNCI):
    """DB palsu: tepat satu lampiran di `sumber`, milik (lampiran, INDUK,
    pemilik). SQL lain / params lain -> tak ada baris."""

    def fr(sql, args):
        if sumber not in sql or "file_path" not in sql:
            return None
        if tuple(args) == (lampiran, INDUK, pemilik):
            return {
                "file_name": 'nota "a"\r\nSet-Cookie: x=1.jpg',
                "file_path": file_path,
                "file_type": "image/jpeg",
                "storage_type": storage_type,
            }
        return None

    return fr


async def isi_stream(resp):
    out = b""
    async for ch in resp.body_iterator:
        out += ch
    return out


def _sql_unduh(conn):
    return [(c[1], c[2]) for c in conn.calls if c[0] == "fetchrow"]


@pytest.mark.asyncio
async def test_si_download_sql_pagar(monkeypatch):
    conn = FakeConn(on_fetchrow=db_satu_lampiran("sales_invoice_attachments", LAMPIRAN))
    pasang(monkeypatch, si, conn)
    await si.download_invoice_attachment(req(), INDUK, LAMPIRAN)
    (sql, args), = _sql_unduh(conn)
    assert re.search(r"JOIN\s+sales_invoices\s+si\s+ON\s+si\.id\s*=\s*sa\.invoice_id", sql)
    assert re.search(r"si\.tenant_id\s*=\s*\$3", sql)
    assert re.search(r"sa\.id\s*=\s*\$1", sql)
    assert re.search(r"sa\.invoice_id\s*=\s*\$2", sql)
    assert args == (LAMPIRAN, INDUK, TENANT)


@pytest.mark.asyncio
async def test_bills_download_sql_pagar_dua_sumber(monkeypatch):
    # lampiran di sumber kedua -> sumber pertama dicoba dulu, lalu documents
    conn = FakeConn(on_fetchrow=db_satu_lampiran("document_attachments", LAMPIRAN_DOK))
    pasang(monkeypatch, bl, conn)
    resp = await bl.download_bill_attachment(req(), INDUK, LAMPIRAN_DOK)
    assert isinstance(resp, StreamingResponse)
    q = _sql_unduh(conn)
    assert len(q) == 2
    (sql1, a1), (sql2, a2) = q
    assert "FROM bill_attachments ba" in sql1
    assert re.search(r"JOIN\s+bills\s+b\s+ON\s+b\.id\s*=\s*ba\.bill_id", sql1)
    assert re.search(r"b\.tenant_id\s*=\s*\$3", sql1)
    assert re.search(r"ba\.id\s*=\s*\$1", sql1) and re.search(r"ba\.bill_id\s*=\s*\$2", sql1)
    assert re.search(r"JOIN\s+bills\s+b\s+ON\s+b\.id\s*=\s*da\.entity_id", sql2)
    assert re.search(r"b\.tenant_id\s*=\s*\$3", sql2)
    assert re.search(r"d\.id\s*=\s*\$1", sql2) and re.search(r"da\.entity_id\s*=\s*\$2", sql2)
    assert "da.entity_type = 'bill'" in sql2
    assert "deleted_at IS NULL" in sql2
    assert a1 == a2 == (LAMPIRAN_DOK, INDUK, TENANT)


@pytest.mark.asyncio
async def test_bills_download_sumber_lama_tak_menyentuh_documents(monkeypatch):
    conn = FakeConn(on_fetchrow=db_satu_lampiran("bill_attachments", LAMPIRAN))
    pasang(monkeypatch, bl, conn)
    await bl.download_bill_attachment(req(), INDUK, LAMPIRAN)
    q = _sql_unduh(conn)
    assert len(q) == 1 and "bill_attachments" in q[0][0]


@pytest.mark.asyncio
async def test_exp_download_sql_pagar(monkeypatch):
    conn = FakeConn(on_fetchrow=db_satu_lampiran("document_attachments", LAMPIRAN_DOK))
    pasang(monkeypatch, ex, conn)
    await ex.download_expense_attachment(req(), INDUK, LAMPIRAN_DOK)
    (sql, args), = _sql_unduh(conn)
    assert re.search(r"JOIN\s+expenses\s+e\s+ON\s+e\.id\s*=\s*da\.entity_id", sql)
    assert re.search(r"e\.tenant_id\s*=\s*\$3", sql)
    assert re.search(r"d\.id\s*=\s*\$1", sql)
    assert re.search(r"da\.entity_id\s*=\s*\$2", sql)
    assert "da.entity_type = 'expense'" in sql
    assert "deleted_at IS NULL" in sql
    assert args == (LAMPIRAN_DOK, INDUK, TENANT)


@pytest.mark.asyncio
@pytest.mark.parametrize("_id,mod,fn,lamp,sumber", RUTE, ids=IDS)
async def test_download_tenant_lain_404(monkeypatch, _id, mod, fn, lamp, sumber):
    conn = FakeConn(on_fetchrow=db_satu_lampiran(sumber, lamp, pemilik=TENANT))
    storage = pasang(monkeypatch, mod, conn)
    with pytest.raises(HTTPException) as ei:
        await getattr(mod, fn)(req(TENANT_LAIN), INDUK, lamp)
    assert ei.value.status_code == 404
    assert storage.client.get_calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("_id,mod,fn,lamp,sumber", RUTE, ids=IDS)
async def test_download_induk_lain_404(monkeypatch, _id, mod, fn, lamp, sumber):
    conn = FakeConn(on_fetchrow=db_satu_lampiran(sumber, lamp))
    storage = pasang(monkeypatch, mod, conn)
    with pytest.raises(HTTPException) as ei:
        await getattr(mod, fn)(req(), uuid.uuid4(), lamp)
    assert ei.value.status_code == 404
    assert storage.client.get_calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "_id,mod,fn,lamp,sumber", [r for r in RUTE if r[4] == "document_attachments"],
    ids=[r[0] for r in RUTE if r[4] == "document_attachments"],
)
async def test_download_baris_local_404_tanpa_storage(monkeypatch, _id, mod, fn, lamp, sumber):
    conn = FakeConn(on_fetchrow=db_satu_lampiran(sumber, lamp, storage_type="local"))
    storage = pasang(monkeypatch, mod, conn)
    with pytest.raises(HTTPException) as ei:
        await getattr(mod, fn)(req(), INDUK, lamp)
    assert ei.value.status_code == 404
    assert ei.value.detail == "Berkas tidak tersedia"
    assert storage.client.get_calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("_id,mod,fn,lamp,sumber", RUTE, ids=IDS)
async def test_download_objek_hilang_404_bukan_500(monkeypatch, _id, mod, fn, lamp, sumber):
    conn = FakeConn(on_fetchrow=db_satu_lampiran(sumber, lamp))
    storage = pasang(monkeypatch, mod, conn, hilang=True)
    with pytest.raises(HTTPException) as ei:
        await getattr(mod, fn)(req(), INDUK, lamp)
    assert ei.value.status_code == 404
    assert ei.value.detail == "Berkas tidak tersedia"
    assert storage.client.get_calls == [("milkyhoop-documents", KUNCI)]


@pytest.mark.asyncio
@pytest.mark.parametrize("_id,mod,fn,lamp,sumber", RUTE, ids=IDS)
async def test_download_file_path_kosong_404(monkeypatch, _id, mod, fn, lamp, sumber):
    conn = FakeConn(on_fetchrow=db_satu_lampiran(sumber, lamp, file_path=None))
    storage = pasang(monkeypatch, mod, conn)
    with pytest.raises(HTTPException) as ei:
        await getattr(mod, fn)(req(), INDUK, lamp)
    assert ei.value.status_code == 404
    assert storage.client.get_calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("_id,mod,fn,lamp,sumber", RUTE, ids=IDS)
async def test_download_sah_stream_isi_storage(monkeypatch, _id, mod, fn, lamp, sumber):
    conn = FakeConn(on_fetchrow=db_satu_lampiran(sumber, lamp))
    storage = pasang(monkeypatch, mod, conn)
    resp = await getattr(mod, fn)(req(), INDUK, lamp)
    assert isinstance(resp, StreamingResponse)
    assert storage.client.get_calls == [("milkyhoop-documents", KUNCI)]
    assert await isi_stream(resp) == ISI
    assert resp.media_type == "image/jpeg"
    cdisp = resp.headers["content-disposition"]
    assert "\r" not in cdisp and "\n" not in cdisp
    m = re.match(r'inline; filename="([^"]*)"; filename\*=UTF-8\'\'(\S+)$', cdisp)
    assert m, cdisp
    assert "private" in resp.headers["cache-control"]
    assert storage.presign_calls == []


# ================================================== 3. izin READ
def _resolver():
    return pm.PermissionMiddleware(app=None)


@pytest.mark.parametrize(
    "prefix,modul_izin",
    [
        ("/api/sales-invoices", "sales_invoice"),
        ("/api/bills", "purchase_invoice"),
        ("/api/expenses", "expense"),
    ],
)
def test_izin_read_download_sama_dengan_daftar_lampiran(prefix, modul_izin):
    mw = _resolver()
    daftar = f"{prefix}/{INDUK}/attachments"
    unduh = f"{prefix}/{INDUK}/attachments/{LAMPIRAN}/download"
    assert mw._find_permission(daftar, "GET") == (modul_izin, "R")
    assert mw._find_permission(unduh, "GET") == (modul_izin, "R")
    assert not any(p.match(unduh) for p in mw._compiled_read_open)
    assert not any(p.match(unduh) for p in mw._compiled_skip)
