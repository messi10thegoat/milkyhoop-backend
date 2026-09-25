"""Unit 2 — PDF dokumen `?format=url` = path relatif gateway, bukan presign MinIO.

Latar (diukur 25 Sep 2026, baca saja):
  - GET /api/{sales-invoices,bills,quotes}/{id}/pdf?format=url mengunggah PDF
    ke MinIO (`storage.upload_bytes`) lalu mengembalikan URL presign
    (host MINIO_PUBLIC_ENDPOINT :9000). MinIO hanya 127.0.0.1:9000 sejak
    23 Sep -> URL itu MATI dari luar.
  - `format=url` adalah BAWAAN faktur penjualan & pembelian: pemanggil tanpa
    `format` (nginx 23 Sep 10:59Z, browser) menerima url mati.
  - Tiap panggilan meninggalkan salinan PDF tak terbaca di bucket
    (3 objek: `<t>/invoices/<id>.pdf`, kaos 2 + grapgrap 1).
  - Tagihan (bills) dulu MENELAN galat unggah dan jatuh ke PDF inline: bentuk
    respons berganti tanpa tanda.

Arah: `url` = `/api/<modul>/<id>/pdf?format=inline[&template=x]`; tanpa
tulisan MinIO; `expires_at` None (path gateway tak kedaluwarsa, berpagar
izin + tenant di tiap unduhan). Helper tunggal `app/utils/pdf_url.py`.

Handler tagihan dipanggil PENUH (layanan/pool/pdf/storage palsu) supaya
penyambungannya terjaga; faktur penjualan & penawaran terlalu berat dipanggil
utuh -> kontrak AST pada fungsi handlernya (tanpa panggilan storage, memanggil
helper dengan segmen modul yang cocok dengan prefix mount di main.py).
"""
import ast
import re
import uuid
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.responses import StreamingResponse

from app.routers import bills as bl
from app.utils import pdf_url as pu

TENANT = "kaos-biru-konveksi"
USER_ID = "22222222-2222-2222-2222-222222222222"
BILL_ID = uuid.UUID("11111111-1111-1111-1111-111111111111")
PDF = b"%PDF-1.7 palsu"
APP = Path(pu.__file__).resolve().parents[1]

# (berkas router, nama handler, segmen modul, format bawaan)
HANDLER = [
    ("routers/sales_invoices.py", "get_invoice_pdf", "sales-invoices", "url"),
    ("routers/bills.py", "get_bill_pdf", "bills", "url"),
    ("routers/quotes.py", "get_quote_pdf", "quotes", "inline"),
]
PANGGILAN_STORAGE = {"upload_bytes", "generate_signed_url", "get_storage_service",
                     "upload_file", "put_object"}


# ------------------------------------------------------------------ helper
def test_url_relatif_ke_rute_inline():
    u = pu.url_pdf_dokumen("bills", BILL_ID)
    assert u == f"/api/bills/{BILL_ID}/pdf?format=inline"
    p = urlparse(u)
    assert not p.scheme and not p.netloc
    assert ":9000" not in u and "X-Amz" not in u


def test_template_hanya_bila_dikirim():
    assert "template" not in pu.url_pdf_dokumen("sales-invoices", BILL_ID)
    assert "template" not in pu.url_pdf_dokumen("sales-invoices", BILL_ID, None)
    q = parse_qs(urlparse(pu.url_pdf_dokumen("sales-invoices", BILL_ID, "b")).query)
    assert q == {"format": ["inline"], "template": ["b"]}


def test_respons_bentuk_lama_tanpa_kedaluwarsa():
    r = pu.respons_pdf_url("quotes", "q-1", "PNW-0001.pdf")
    assert r["success"] is True
    assert set(r["data"]) == {"status", "url", "expires_at", "filename"}
    assert r["data"]["url"] == "/api/quotes/q-1/pdf?format=inline"
    assert r["data"]["expires_at"] is None
    assert r["data"]["filename"] == "PNW-0001.pdf"
    # dulu faktur penjualan menjawab literal "draft" di sini (#43 catatan pdf-url)
    assert r["data"]["status"] == "ok"


# ------------------------------------------------- tagihan: handler penuh
class _Ctx:
    def __init__(self, val):
        self.val = val

    async def __aenter__(self):
        return self.val

    async def __aexit__(self, *a):
        return False


class FakeConn:
    async def execute(self, sql, *args):
        return None

    async def fetchrow(self, sql, *args):
        return {"display_name": "Kaos Biru", "address": None, "phone": None,
                "logo_url": None}


class FakePool:
    def acquire(self):
        return _Ctx(FakeConn())


class FakeStorage:
    """Mencatat SETIAP sentuhan storage; unit ini menuntut nol."""

    def __init__(self):
        self.calls = []
        self.config = SimpleNamespace(bucket="milkyhoop-documents", url_expiry=3600,
                                      thumbnail_expiry=3600)

    async def upload_bytes(self, **kw):
        self.calls.append(("upload_bytes", kw.get("file_path")))
        return "http://159.89.202.160:9000/milkyhoop-documents/x?X-Amz-Signature=a"

    async def generate_signed_url(self, file_path, expires_in=None):
        self.calls.append(("generate_signed_url", file_path))
        return "http://159.89.202.160:9000/x"


@pytest.fixture
def tagihan(monkeypatch):
    storage = FakeStorage()

    class Svc:
        async def get_bill_v2(self, tenant_id, bill_id):
            assert tenant_id == TENANT
            return {"id": str(bill_id), "invoice_number": "BILL/2609/0007"}

    async def _svc():
        return Svc()

    async def _pool():
        return FakePool()

    monkeypatch.setattr(bl, "get_bills_service", _svc)
    monkeypatch.setattr(bl, "get_pool", _pool)
    monkeypatch.setattr(bl, "get_pdf_service",
                        lambda: SimpleNamespace(generate_bill_pdf=lambda b: PDF))
    monkeypatch.setattr(bl, "get_storage_service", lambda: storage)
    import app.services.storage_service as ss
    monkeypatch.setattr(ss, "get_storage_service", lambda: storage)
    return storage


def _req():
    return SimpleNamespace(state=SimpleNamespace(
        user={"tenant_id": TENANT, "user_id": USER_ID}))


@pytest.mark.asyncio
async def test_tagihan_format_url_path_gateway_tanpa_storage(tagihan):
    r = await bl.get_bill_pdf(_req(), BILL_ID, format="url")
    assert isinstance(r, dict), f"bukan JSON: {type(r).__name__}"
    assert r["data"]["url"] == f"/api/bills/{BILL_ID}/pdf?format=inline"
    assert r["data"]["expires_at"] is None
    assert r["data"]["filename"].endswith(".pdf") and "0007" in r["data"]["filename"]
    assert tagihan.calls == [], f"storage disentuh: {tagihan.calls}"


@pytest.mark.asyncio
async def test_tagihan_url_mengarah_ke_rute_yang_menyajikan_pdf(tagihan):
    r = await bl.get_bill_pdf(_req(), BILL_ID, format="url")
    q = parse_qs(urlparse(r["data"]["url"]).query)
    inline = await bl.get_bill_pdf(_req(), BILL_ID, format=q["format"][0])
    assert isinstance(inline, StreamingResponse)
    assert inline.media_type == "application/pdf"
    assert tagihan.calls == []


# --------------------------------------------- kontrak AST ketiga handler
def _fungsi(berkas, nama):
    tree = ast.parse((APP / berkas).read_text())
    for n in ast.walk(tree):
        if isinstance(n, ast.AsyncFunctionDef) and n.name == nama:
            return n
    raise AssertionError(f"{nama} tak ada di {berkas}")


def _nama_panggilan(fn):
    out = []
    for n in ast.walk(fn):
        if isinstance(n, ast.Call):
            f = n.func
            out.append(f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", None))
    return out


@pytest.mark.parametrize("berkas,nama,modul,bawaan", HANDLER)
def test_handler_tanpa_storage(berkas, nama, modul, bawaan):
    fn = _fungsi(berkas, nama)
    kena = sorted(set(_nama_panggilan(fn)) & PANGGILAN_STORAGE)
    assert kena == [], f"{nama} masih menyentuh storage: {kena}"


@pytest.mark.parametrize("berkas,nama,modul,bawaan", HANDLER)
def test_handler_memanggil_helper_dengan_modul_benar(berkas, nama, modul, bawaan):
    fn = _fungsi(berkas, nama)
    calls = [n for n in ast.walk(fn) if isinstance(n, ast.Call)
             and getattr(n.func, "id", None) == "respons_pdf_url"]
    assert len(calls) == 1, f"{nama}: respons_pdf_url dipanggil {len(calls)}x"
    arg0 = calls[0].args[0]
    assert isinstance(arg0, ast.Constant) and arg0.value == modul
    # segmen modul == prefix mount sebenarnya
    main = (APP / "main.py").read_text()
    mod_py = Path(berkas).stem
    assert re.search(rf"{mod_py}\.router,\s*prefix=\"/api/{re.escape(modul)}\"", main), \
        f"prefix /api/{modul} untuk {mod_py} tak ditemukan di main.py"


@pytest.mark.parametrize("berkas,nama,modul,bawaan", HANDLER)
def test_format_bawaan_tidak_berubah(berkas, nama, modul, bawaan):
    """Kontrak lama dipertahankan: faktur penjualan/pembelian bawaan 'url',
    penawaran 'inline'. Yang berubah hanya ISI url."""
    fn = _fungsi(berkas, nama)
    arg = next(a for a in fn.args.args if a.arg == "format")
    i = fn.args.args.index(arg) - (len(fn.args.args) - len(fn.args.defaults))
    d = fn.args.defaults[i]
    assert isinstance(d, ast.Call) and d.args[0].value == bawaan


def test_faktur_penjualan_meneruskan_template():
    fn = _fungsi("routers/sales_invoices.py", "get_invoice_pdf")
    call = next(n for n in ast.walk(fn) if isinstance(n, ast.Call)
                and getattr(n.func, "id", None) == "respons_pdf_url")
    assert [getattr(a, "id", None) for a in call.args[1:]] == \
        ["invoice_id", "filename", "template"]
