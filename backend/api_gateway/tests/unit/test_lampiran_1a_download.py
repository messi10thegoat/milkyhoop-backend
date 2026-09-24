"""Unit 1a — lampiran keluarga pembayaran diunduh LEWAT GATEWAY.

Latar (diukur 24 Sep 2026): MinIO kini hanya 127.0.0.1:9000, sedangkan URL
presign ber-host MINIO_PUBLIC_ENDPOINT (159.89.202.160:9000) -> mati dari luar.
Detail penerimaan malah mengirim `documents.file_url` mentah (NULL untuk 26
baris s3). Arah: `url` = path relatif `/api/<modul>/{id}/attachments/{aid}/download`
yang men-STREAM lewat gateway.

Yang dibuktikan di sini (handler dipanggil PENUH dengan pool/konektor/storage
palsu -- bukan helper saja, supaya penyambungannya ikut terjaga):
  1. daftar/unggah/detail: url == path relatif, tak pernah ':9000'/'http'/kunci
     storage;
  2. rute download: SQL memuat JOIN induk modulnya sendiri + tenant JWT; tak
     cocok -> 404; baris local -> 404 tanpa menyentuh storage; baris sah ->
     StreamingResponse berisi objek storage; header nama berkas tak bisa
     dipecah;
  3. izin READ: tiap rute download dipetakan ke modul yang SAMA dengan rute
     daftar lampirannya (bukan unmapped / default-tertutup / allowlist).

entity_type 'payment' DIPAKAI BERSAMA receive_payments dan bill_payments_v2:
tes pagar induk memastikan tiap rute meng-JOIN tabel modulnya sendiri.
"""
import ast
import io
import re
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.responses import StreamingResponse
from starlette.datastructures import Headers, UploadFile

from app.routers import bill_payments as bp
from app.routers import customer_deposits as cd
from app.routers import receive_payments as rp
from app.middleware import permission_middleware as pm

TENANT = "kaos-biru-konveksi"
TENANT_LAIN = "tenant-lain"
USER_ID = "22222222-2222-2222-2222-222222222222"
INDUK = uuid.UUID("11111111-1111-1111-1111-111111111111")
LAMPIRAN = uuid.UUID("33333333-3333-3333-3333-333333333333")
KUNCI = "kaos-biru-konveksi/other/2026/09/fe7a49c5_IMG_0701.jpg"
PRESIGN = (
    "http://159.89.202.160:9000/milkyhoop-documents/"
    + KUNCI
    + "?X-Amz-Signature=abc"
)
ISI = b"\x89PNG isi-berkas-lampiran" * 5000  # > 64 KiB -> lebih dari satu potong


# ----------------------------------------------------------------- palsu
class FakeConn:
    """Konektor palsu: merekam SQL + params. `fetchrow` / `fetch` dijawab oleh
    fungsi yang ditentukan tiap tes (menirukan DB)."""

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
        # L2: rute unggah DP menghitung kuota lampiran. Hanya SQL itu yang
        # dijawab (0 terpakai); SQL lain = tes tak mengenalnya -> gagal keras.
        self.calls.append(("fetchval", sql, args))
        from app.attachment_limits import SQL_HITUNG_LAMPIRAN_TERSEDIA

        if sql == SQL_HITUNG_LAMPIRAN_TERSEDIA:
            return 0
        raise AssertionError(f"fetchval tak dikenal: {sql}")

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


class FakeClient:
    def __init__(self, data=ISI):
        self.data = data
        self.get_calls = []

    def get_object(self, Bucket, Key):
        self.get_calls.append((Bucket, Key))
        return {"Body": FakeBody(self.data)}


class FakeStorage:
    def __init__(self):
        self.client = FakeClient()
        self.config = SimpleNamespace(bucket="milkyhoop-documents")
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


def pasang(monkeypatch, mod, conn):
    storage = FakeStorage()

    async def _pool():
        return FakePool(conn)

    monkeypatch.setattr(mod, "get_pool", _pool)
    monkeypatch.setattr(mod, "get_storage_service", lambda: storage)
    return storage


def baris_dok(**kw):
    """Baris gabungan documents x document_attachments -- sengaja membawa
    SEMUA medan mentah (file_path, file_url, url) supaya kembalinya kode ke
    presign / file_url mentah langsung terlihat."""
    from datetime import datetime, timezone

    r = {
        "id": LAMPIRAN,
        "file_name": "bukti transfer.jpg",
        "file_size": 1234,
        "file_type": "image/jpeg",
        "mime_type": "image/jpeg",
        "file_path": KUNCI,
        "file_url": PRESIGN,
        "url": PRESIGN,
        "thumbnail_url": None,
        "description": None,
        "uploaded_at": datetime(2026, 9, 24, tzinfo=timezone.utc),
        "created_at": datetime(2026, 9, 24, tzinfo=timezone.utc),
        "uploaded_by": None,
        "uploaded_by_name": "Pemilik",
        "attachment_type": "receipt",
        "display_order": 0,
    }
    r.update(kw)
    return r


def url_sah(url, modul, induk=INDUK, lampiran=LAMPIRAN):
    assert url == f"/api/{modul}/{induk}/attachments/{lampiran}/download", url
    assert ":9000" not in url
    assert "http" not in url
    assert KUNCI not in url and "kaos-biru-konveksi/" not in url


# ======================================================= 1. url di respons
@pytest.mark.asyncio
async def test_cd_daftar_url_relatif_bukan_presign(monkeypatch):
    conn = FakeConn(
        on_fetchrow=lambda s, a: {"id": INDUK},
        on_fetch=lambda s, a: [baris_dok()],
    )
    storage = pasang(monkeypatch, cd, conn)
    out = await cd.list_deposit_attachments(req(), INDUK)
    assert len(out["attachments"]) == 1
    url_sah(out["attachments"][0]["url"], "customer-deposits")
    assert storage.presign_calls == []


@pytest.mark.asyncio
async def test_cd_unggah_url_relatif_bukan_presign(monkeypatch):
    conn = FakeConn(on_fetchrow=lambda s, a: {"id": INDUK})
    pasang(monkeypatch, cd, conn)
    f = UploadFile(
        file=io.BytesIO(b"\x89PNG\r\n\x1a\n kecil"),  # L2: tanda tangan PNG SAH (byte awal diperiksa)
        filename="bukti.png",
        headers=Headers({"content-type": "image/png"}),
    )
    out = await cd.upload_deposit_attachment(req(), INDUK, f)
    dok_id = out["data"]["id"]
    url_sah(out["data"]["url"], "customer-deposits", lampiran=dok_id)


@pytest.mark.asyncio
async def test_bp_daftar_url_relatif_dan_join_induk(monkeypatch):
    conn = FakeConn(on_fetch=lambda s, a: [baris_dok()])
    storage = pasang(monkeypatch, bp, conn)
    out = await bp.list_payment_attachments(req(), str(INDUK))
    assert len(out["data"]) == 1
    url_sah(out["data"][0]["url"], "bill-payments")
    assert storage.presign_calls == []
    sql, args = [(c[1], c[2]) for c in conn.calls if c[0] == "fetch"][0]
    assert re.search(r"JOIN\s+bill_payments_v2\s+bp\s+ON\s+bp\.id\s*=\s*da\.entity_id", sql)
    assert re.search(r"bp\.tenant_id\s*=\s*\$2", sql)
    assert args == (INDUK, TENANT)


@pytest.mark.asyncio
async def test_bp_documents_file_url_relatif_dan_kolom_ada(monkeypatch):
    conn = FakeConn(
        on_fetchrow=lambda s, a: {"id": INDUK, "payment_number": "PAY-1"},
        on_fetch=lambda s, a: [baris_dok()],
    )
    pasang(monkeypatch, bp, conn)
    out = await bp.get_payment_documents(req(), str(INDUK))
    assert out["total"] == 1
    url_sah(out["data"][0]["file_url"], "bill-payments")
    # Kolom yang dulu dibaca TIDAK ADA di `documents` (information_schema 24 Sep).
    for _, sql, _ in conn.calls:
        assert not re.search(r"(?<![a-z_.])entity_type\s*=\s*'payment'\s+AND\s+entity_id", sql)
        assert "created_by" not in sql


@pytest.mark.asyncio
async def test_rp_daftar_url_relatif_dan_join_induk(monkeypatch):
    conn = FakeConn(on_fetch=lambda s, a: [baris_dok()])
    pasang(monkeypatch, rp, conn)
    out = await rp.list_payment_attachments(req(), str(INDUK))
    assert len(out["data"]) == 1
    url_sah(out["data"][0]["url"], "receive-payments")
    sql, args = [(c[1], c[2]) for c in conn.calls if c[0] == "fetch"][0]
    assert re.search(r"JOIN\s+receive_payments\s+rp\s+ON\s+rp\.id\s*=\s*da\.entity_id", sql)
    assert re.search(r"rp\.tenant_id\s*=\s*\$2", sql)
    assert args == (INDUK, TENANT)


def test_rp_pembentuk_lampiran_detail():
    out = rp._rp_lampiran_ke_respons([baris_dok()], INDUK)
    url_sah(out[0]["url"], "receive-payments")
    # induk bukan receive_payments (jalur jurnal-saja) -> None, bukan file_url
    assert rp._rp_lampiran_ke_respons([baris_dok()], None)[0]["url"] is None


def test_rp_detail_menyambung_ke_pembentuk_lampiran():
    """Kontrak AST: detail GET /api/receive-payments/{id} membentuk lampiran
    LEWAT `_rp_lampiran_ke_respons` di kedua jalurnya (normal + jurnal-saja),
    dan tak ada lagi medan "url" yang mengambil r["url"]/file_url mentah."""
    src = Path(rp.__file__).read_text(encoding="utf-8")
    fn = next(
        n for n in ast.walk(ast.parse(src))
        if isinstance(n, ast.AsyncFunctionDef) and n.name == "get_receive_payment"
    )
    panggil = [
        n for n in ast.walk(fn)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Name)
        and n.func.id == "_rp_lampiran_ke_respons"
    ]
    arg2 = sorted(ast.unparse(c.args[1]) for c in panggil)
    assert arg2 == ["None", "payment['id']"], arg2
    for n in ast.walk(fn):
        if isinstance(n, ast.Dict):
            for k, v in zip(n.keys, n.values):
                if isinstance(k, ast.Constant) and k.value in ("url", "file_url"):
                    teks = ast.unparse(v)
                    assert "r[" not in teks and "file_url" not in teks, teks
    teks_fn = ast.unparse(fn)
    assert "file_url" not in teks_fn
    assert "generate_signed_url" not in teks_fn


# ======================================================= 2. rute download
# (modul, fungsi download, tabel induk, alias, entity_type)
RUTE = [
    (cd, "download_deposit_attachment", "customer_deposits", "cd", "customer_deposit"),
    (bp, "download_payment_attachment", "bill_payments_v2", "bp", "payment"),
    (rp, "download_payment_attachment", "receive_payments", "rp", "payment"),
]
IDS = ["customer-deposits", "bill-payments", "receive-payments"]


def db_satu_lampiran(storage_type="s3", pemilik=TENANT):
    """DB palsu: tepat satu lampiran, milik (LAMPIRAN, INDUK, pemilik)."""

    def fr(sql, args):
        if "storage_type" not in sql:
            return None
        if tuple(args) == (LAMPIRAN, INDUK, pemilik):
            return {
                "file_name": 'bukti "a"\r\nSet-Cookie: x=1.jpg',
                "file_path": KUNCI,
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


@pytest.mark.asyncio
@pytest.mark.parametrize("mod,fn,tabel,alias,etype", RUTE, ids=IDS)
async def test_download_sql_pagar_induk_dan_tenant(monkeypatch, mod, fn, tabel, alias, etype):
    conn = FakeConn(on_fetchrow=db_satu_lampiran())
    pasang(monkeypatch, mod, conn)
    await getattr(mod, fn)(req(), INDUK, LAMPIRAN)
    q = [(c[1], c[2]) for c in conn.calls if c[0] == "fetchrow"]
    assert len(q) == 1
    sql, args = q[0]
    # induk = tabel MODUL INI (entity_type 'payment' dipakai bersama)
    assert re.search(
        rf"JOIN\s+{tabel}\s+{alias}\s+ON\s+{alias}\.id\s*=\s*da\.entity_id", sql
    ), sql
    for lain in {"customer_deposits", "bill_payments_v2", "receive_payments"} - {tabel}:
        assert lain not in sql
    assert re.search(rf"{alias}\.tenant_id\s*=\s*\$3", sql)
    assert re.search(r"d\.id\s*=\s*\$1", sql)
    assert re.search(r"da\.entity_id\s*=\s*\$2", sql)
    assert f"da.entity_type = '{etype}'" in sql
    assert "deleted_at IS NULL" in sql
    # tenant diteruskan dari JWT, bukan dari mana pun lainnya
    assert args == (LAMPIRAN, INDUK, TENANT)


@pytest.mark.asyncio
@pytest.mark.parametrize("mod,fn,tabel,alias,etype", RUTE, ids=IDS)
async def test_download_tenant_lain_404(monkeypatch, mod, fn, tabel, alias, etype):
    conn = FakeConn(on_fetchrow=db_satu_lampiran(pemilik=TENANT))
    storage = pasang(monkeypatch, mod, conn)
    with pytest.raises(HTTPException) as ei:
        await getattr(mod, fn)(req(TENANT_LAIN), INDUK, LAMPIRAN)
    assert ei.value.status_code == 404
    assert storage.client.get_calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("mod,fn,tabel,alias,etype", RUTE, ids=IDS)
async def test_download_induk_lain_404(monkeypatch, mod, fn, tabel, alias, etype):
    conn = FakeConn(on_fetchrow=db_satu_lampiran())
    storage = pasang(monkeypatch, mod, conn)
    with pytest.raises(HTTPException) as ei:
        await getattr(mod, fn)(req(), uuid.uuid4(), LAMPIRAN)
    assert ei.value.status_code == 404
    assert storage.client.get_calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("mod,fn,tabel,alias,etype", RUTE, ids=IDS)
async def test_download_baris_local_404_tanpa_storage(monkeypatch, mod, fn, tabel, alias, etype):
    conn = FakeConn(on_fetchrow=db_satu_lampiran(storage_type="local"))
    storage = pasang(monkeypatch, mod, conn)
    with pytest.raises(HTTPException) as ei:
        await getattr(mod, fn)(req(), INDUK, LAMPIRAN)
    assert ei.value.status_code == 404
    assert ei.value.detail == "Berkas tidak tersedia"
    assert storage.client.get_calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("mod,fn,tabel,alias,etype", RUTE, ids=IDS)
async def test_download_sah_stream_isi_storage(monkeypatch, mod, fn, tabel, alias, etype):
    conn = FakeConn(on_fetchrow=db_satu_lampiran())
    storage = pasang(monkeypatch, mod, conn)
    resp = await getattr(mod, fn)(req(), INDUK, LAMPIRAN)
    assert isinstance(resp, StreamingResponse)
    assert storage.client.get_calls == [("milkyhoop-documents", KUNCI)]
    assert await isi_stream(resp) == ISI
    assert resp.media_type == "image/jpeg"
    cdisp = resp.headers["content-disposition"]
    assert cdisp.startswith("inline; filename=")
    assert "\r" not in cdisp and "\n" not in cdisp
    # nama asli ber-kutip tak boleh memutus kutip filename=
    m = re.match(r'inline; filename="([^"]*)"; filename\*=UTF-8\'\'(\S+)$', cdisp)
    assert m, cdisp
    assert "private" in resp.headers["cache-control"]


# ======================================================= 3. izin READ
def _resolver():
    return pm.PermissionMiddleware(app=None)


@pytest.mark.parametrize(
    "prefix,modul_izin",
    [
        ("/api/customer-deposits", "customer_deposit"),
        ("/api/bill-payments", "send_payment"),
        ("/api/receive-payments", "receive_payment"),
    ],
)
def test_izin_read_download_sama_dengan_daftar_lampiran(prefix, modul_izin):
    mw = _resolver()
    daftar = f"{prefix}/{INDUK}/attachments"
    unduh = f"{prefix}/{INDUK}/attachments/{LAMPIRAN}/download"
    assert mw._find_permission(daftar, "GET") == (modul_izin, "R")
    # terpetakan (bukan unmapped -> default-TERTUTUP STEP 2) ke modul yang sama
    assert mw._find_permission(unduh, "GET") == (modul_izin, "R")
    # dan BUKAN lewat allowlist READ terbuka
    assert not any(p.match(unduh) for p in mw._compiled_read_open)
    assert not any(p.match(unduh) for p in mw._compiled_skip)
