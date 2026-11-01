"""VENDOR-DUPLIKAT (1 Nov 2026, putusan MASTER): satu bentuk galat duplikat pelanggan & vendor + check-duplicate vendor.

SPEK:
- 409 badan PERSIS {"detail": <kalimat>, "code", "message": <kalimat>, "field"}; detail STRING (pembaca detail-string FE).
- vendor: predikat == indeks unik DB (nama: SEMUA vendor termasuk nonaktif; kode: code IS NOT NULL); nonaktif disebut.
- PATCH vendor: nama DAN kode yang berubah dicek (dulu kode -> 500 DB).
- GET /api/vendors/check-duplicate: cocok -> 200 daftar; kosong -> 200 kosong; tenant tersaring; nonaktif ikut (is_active);
  ILIKE ber-escape.
- handler main.py (TestClient asli): status + header + badan JSON persis.
- pemindai: tak ada lagi detail dict / "already exists" untuk duplikat pelanggan/vendor.
"""
import ast
import asyncio
import os
import pathlib
import uuid

import pytest
from fastapi import FastAPI, HTTPException

os.environ.setdefault("OPENAI_API_KEY", "sk-boneka-unit-test-tanpa-jaringan")  # impor app.main butuh satu penyedia LLM

from app.services import galat_berkode as GB  # noqa: E402
from app.services import pelanggan_unik as PU  # noqa: E402
from app.services import vendor_unik as VU  # noqa: E402
from app.routers import customers as CU  # noqa: E402
from app.routers import vendors as RV  # noqa: E402

T = "tenant-uji"
APP = pathlib.Path(RV.__file__).resolve().parents[1]


def _j(c):
    return asyncio.run(c)


class Conn:
    def __init__(self, baris=None, rows=None):
        self.baris, self.rows, self.q = baris, rows or [], []

    async def fetchrow(self, sql, *a):
        self.q.append((sql, a))
        return self.baris

    async def fetchval(self, sql, *a):
        self.q.append((sql, a))
        return None if self.baris is None else self.baris.get("id")

    async def fetch(self, sql, *a):
        self.q.append((sql, a))
        return self.rows

    async def execute(self, sql, *a):
        self.q.append((sql, a))


def _galat(coro) -> GB.GalatBerkode:
    with pytest.raises(GB.GalatBerkode) as e:
        _j(coro)
    return e.value


# ---- bentuk ----
def test_badan_galat_berkode_persis_dan_detail_string():
    g = GB.GalatBerkode(409, "KODE_X", "Kalimat.", "name")
    assert isinstance(g, HTTPException) and g.status_code == 409 and g.detail == "Kalimat."
    assert g.badan() == {"detail": "Kalimat.", "code": "KODE_X", "message": "Kalimat.", "field": "name"}
    assert GB.GalatBerkode(409, "KODE_X", "K.").badan() == {"detail": "K.", "code": "KODE_X", "message": "K."}


# ---- vendor ----
def test_nama_vendor_dipakai_aktif_409_persis():
    c = Conn({"id": uuid.uuid4(), "is_active": True})
    g = _galat(VU.tolak_nama_dipakai(c, T, "PT Maju"))
    m = 'Nama vendor "PT Maju" sudah dipakai vendor lain.'
    assert g.status_code == 409 and g.badan() == {"detail": m, "code": "NAMA_VENDOR_DIPAKAI", "message": m, "field": "name"}
    sql, a = c.q[0]
    assert "tenant_id = $1 AND name = $2" in sql and "is_active =" not in sql and a == (T, "PT Maju", None)


def test_nama_vendor_dipakai_nonaktif_disebut():
    g = _galat(VU.tolak_nama_dipakai(Conn({"id": uuid.uuid4(), "is_active": False}), T, "PT Lama"))
    assert g.detail == 'Nama vendor "PT Lama" sudah dipakai vendor nonaktif (aktifkan lagi atau gunakan nama lain).'
    assert g.badan()["code"] == "NAMA_VENDOR_DIPAKAI"


def test_kode_vendor_dipakai_predikat_indeks_dan_kecuali():
    kid = uuid.uuid4()
    c = Conn({"id": uuid.uuid4(), "is_active": True})
    g = _galat(VU.tolak_kode_dipakai(c, T, "V0007", kecuali_id=kid))
    m = 'Kode vendor "V0007" sudah dipakai vendor lain.'
    assert g.badan() == {"detail": m, "code": "KODE_VENDOR_DIPAKAI", "message": m, "field": "code"}
    sql, a = c.q[0]
    assert "code = $2 AND code IS NOT NULL" in sql and "id <> $3::uuid" in sql and a == (T, "V0007", str(kid))


def test_vendor_bebas_dan_kosong_tak_bertanya():
    c = Conn(None)
    _j(VU.tolak_nama_dipakai(c, T, "PT Baru"))
    _j(VU.tolak_kode_dipakai(c, T, "V0009"))
    assert len(c.q) == 2
    c = Conn({"id": uuid.uuid4(), "is_active": True})
    _j(VU.tolak_nama_dipakai(c, T, ""))
    _j(VU.tolak_kode_dipakai(c, T, None))
    assert c.q == []


# ---- pelanggan (satu bentuk) ----
def test_pelanggan_satu_bentuk():
    g = _galat(PU.tolak_nama_dipakai(Conn({"id": uuid.uuid4()}), T, "Budi"))
    m = 'Nama pelanggan "Budi" sudah dipakai pelanggan lain. Gunakan nama lain.'
    assert g.status_code == 409 and g.badan() == {"detail": m, "code": "NAMA_PELANGGAN_DIPAKAI", "message": m, "field": "name"}
    g = _galat(PU.tolak_nomor_dipakai(Conn({"id": uuid.uuid4()}), T, "C0007"))
    m = 'Kode pelanggan "C0007" sudah dipakai pelanggan lain. Gunakan kode lain.'
    assert g.badan() == {"detail": m, "code": "KODE_PELANGGAN_DIPAKAI", "message": m, "field": "code"}


def test_blok_pratinjau_dari_galat_berkode():
    g = GB.GalatBerkode(409, "NAMA_PELANGGAN_DIPAKAI", "x", "name")
    assert CU.blok_dari_galat(g) == {"code": "NAMA_PELANGGAN_DIPAKAI", "message": "x", "field": "name"}
    assert CU.blok_dari_galat(GB.GalatBerkode(409, "K", "y")) == {"code": "K", "message": "y"}


# ---- handler main.py lewat TestClient ----
def test_handler_main_badan_dan_header_persis():
    from starlette.testclient import TestClient
    from app.main import app as utama
    h = utama.exception_handlers.get(GB.GalatBerkode)
    assert h is not None, "handler GalatBerkode tak terpasang di app.main"
    mini = FastAPI()
    mini.add_exception_handler(GB.GalatBerkode, h)

    @mini.get("/x")
    async def x():
        raise GB.GalatBerkode(409, "NAMA_VENDOR_DIPAKAI", 'Nama vendor "A" sudah dipakai vendor lain.', "name", headers={"X-Uji": "1"})

    r = TestClient(mini).get("/x")
    assert r.status_code == 409 and r.headers["x-uji"] == "1" and r.headers["content-type"].startswith("application/json")
    m = 'Nama vendor "A" sudah dipakai vendor lain.'
    assert r.json() == {"detail": m, "code": "NAMA_VENDOR_DIPAKAI", "message": m, "field": "name"}


# ---- check-duplicate ----
class _Pool:
    def __init__(self, c):
        self.c = c

    def acquire(self):
        c = self.c

        class CM:
            async def __aenter__(s): return c
            async def __aexit__(s, *a): return False
        return CM()


def _req():
    from starlette.requests import Request

    async def receive(): return {"type": "http.request", "body": b""}
    r = Request({"type": "http", "method": "GET", "path": "/", "headers": [], "query_string": b""}, receive)
    r.state.user = {"tenant_id": T, "user_id": str(uuid.uuid4()), "role": "OWNER"}
    return r


def _cek(monkeypatch, rows, **kw):
    c = Conn(rows=rows)

    async def gp(): return _Pool(c)
    monkeypatch.setattr(RV, "get_pool", gp)
    kw = {"nama": None, "npwp": None, "excludeId": None, **kw}
    return _j(RV.check_duplicate(_req(), **kw)), c


def test_check_duplicate_cocok_200_daftar_lengkap(monkeypatch):
    vid = uuid.uuid4()
    baris = {"id": vid, "name": "PT Maju", "company_name": "PT Maju Jaya", "display_name": "Maju", "tax_id": "01.234", "is_active": False}
    hasil, c = _cek(monkeypatch, [baris], nama="maju", npwp="01.234")
    harap = {"id": str(vid), "name": "PT Maju", "company_name": "PT Maju Jaya", "display_name": "Maju", "company": "PT Maju Jaya",
             "npwp": "01.234", "is_active": False}
    assert hasil == {"byName": [harap], "byNpwp": [harap]}
    assert all(a[0] == T and "tenant_id = $1" in s and "is_active =" not in s for s, a in c.q)


def test_check_duplicate_kosong_200_kosong(monkeypatch):
    hasil, c = _cek(monkeypatch, [], nama="tak ada")
    assert hasil == {"byName": [], "byNpwp": []} and len(c.q) == 1
    hasil, c = _cek(monkeypatch, [])
    assert hasil == {"byName": [], "byNpwp": []} and c.q == []


def test_check_duplicate_ilike_escape_dan_kecuali(monkeypatch):
    kid = uuid.uuid4()
    _, c = _cek(monkeypatch, [], nama="50%_a\\b", excludeId=str(kid))
    sql, a = c.q[0]
    assert "ESCAPE '\\'" in sql and a == (T, "%50\\%\\_a\\\\b%", kid)


def test_skema_respons_check_duplicate_tak_membuang_medan():
    from app.schemas.vendors import VendorDuplicateItem as I
    assert {"id", "name", "company_name", "display_name", "company", "npwp", "is_active"} <= set(I.model_fields)


# ---- PATCH: nama & kode berubah dicek ----
def test_patch_vendor_cek_nama_dan_kode_yang_berubah():
    src = (APP / "routers/vendors.py").read_text()
    pohon = ast.parse(src)
    fn = next(n for n in ast.walk(pohon) if isinstance(n, ast.AsyncFunctionDef) and n.name == "update_vendor")
    teks = ast.get_source_segment(src, fn)
    assert 'VU.tolak_nama_dipakai(conn, ctx["tenant_id"], body.name, kecuali_id=vendor_id)' in teks
    assert 'VU.tolak_kode_dipakai(conn, ctx["tenant_id"], body.code, kecuali_id=vendor_id)' in teks
    buat = next(n for n in ast.walk(pohon) if isinstance(n, ast.AsyncFunctionDef) and n.name == "create_vendor")
    tb = ast.get_source_segment(src, buat)
    assert 'VU.tolak_nama_dipakai(conn, ctx["tenant_id"], body.name)' in tb and 'VU.tolak_kode_dipakai(conn, ctx["tenant_id"], body.code)' in tb


# ---- pemindai ----
def _pelanggaran(akar=APP):
    buruk = []
    for p in list((akar / "routers").glob("*.py")) + list((akar / "services").glob("*.py")):
        src = p.read_text(errors="ignore")
        for n in ast.walk(ast.parse(src)):
            if not (isinstance(n, ast.Call) and getattr(n.func, "id", getattr(n.func, "attr", None)) == "HTTPException"):
                continue
            seg = ast.get_source_segment(src, n) or ""
            if ("PELANGGAN_DIPAKAI" in seg or "VENDOR_DIPAKAI" in seg or "KODE_NOMOR" in seg or "KODE_NAMA" in seg
                    or ("already exists" in seg and p.name in ("vendors.py", "customers.py") and "Vendor with" in seg)):
                buruk.append(f"{p.name}:{n.lineno}")
    return buruk


def test_pemindai_tak_ada_galat_duplikat_bentuk_lama():
    assert _pelanggaran() == []


def test_pemindai_bisa_merah(tmp_path):
    (tmp_path / "routers").mkdir()
    (tmp_path / "services").mkdir()
    (tmp_path / "routers" / "vendors.py").write_text(
        "def f(n):\n    raise HTTPException(status_code=400, detail=f\"Vendor with name '{n}' already exists\")\n")
    (tmp_path / "services" / "x.py").write_text(
        "def g():\n    raise HTTPException(status_code=409, detail={'code': KODE_NAMA, 'message': 'm'})\n")
    assert sorted(_pelanggaran(tmp_path)) == ["vendors.py:2", "x.py:2"]
