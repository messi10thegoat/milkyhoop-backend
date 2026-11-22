"""BUG-338A pasca: POST /api/customer-categories/{id}/delete/preview -- lewat rute ASLI (FastAPI TestClient), read-only, penentu SAMA dgn DELETE."""
import re
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.routers import customer_categories as CC
from app.services import kategori_pelanggan as KP
from app.services.galat_berkode import GalatBerkode
from tests.unit.test_kategori_pelanggan_338a import Kon

KID = "11111111-1111-1111-1111-111111111111"


class Tr:
    def __init__(self, c, ro): self.c, self.ro = c, ro
    async def start(self): self.c.tr.append(("start", self.ro))
    async def rollback(self): self.c.tr.append(("rollback", self.ro))
    async def commit(self): self.c.tr.append(("commit", self.ro))
    async def __aenter__(self): await self.start(); return self
    async def __aexit__(self, *a): await self.commit()


class KonT(Kon):
    def __init__(self, **kw):
        super().__init__(**kw); self.tr = []
    def transaction(self, readonly=False): return Tr(self, readonly)


class Pool:
    def __init__(self, c): self.c = c
    def acquire(self):
        c = self.c
        class A:
            async def __aenter__(s): return c
            async def __aexit__(s, *a): return False
        return A()


def _app(monkeypatch, kon, tenant="t-1"):
    async def gp(): return Pool(kon)
    monkeypatch.setattr(CC, "get_pool", gp)
    app = FastAPI()

    @app.middleware("http")
    async def u(request: Request, call_next):
        request.state.user = {"tenant_id": tenant}
        return await call_next(request)
    app.include_router(CC.router, prefix="/api/customer-categories")

    @app.exception_handler(GalatBerkode)
    async def h(request, e):
        from fastapi.responses import JSONResponse
        return JSONResponse(e.badan(), status_code=e.status_code)
    return TestClient(app)


def _tulis(kon): return [q for q, _ in kon.sql if q.startswith(("DELETE", "UPDATE", "INSERT"))]


def test_blok_saat_berpelanggan_read_only_nol_tulis(monkeypatch):
    k = KonT(n_semua=7, n=7, nama=["A", "B", "C", "D", "E"])
    r = _app(monkeypatch, k).post(f"/api/customer-categories/{KID}/delete/preview", json={"x": 1})
    assert r.status_code == 200
    d = r.json()["data"]
    b = d["blocks"][0]
    assert d["ok"] is False and d["rencana"] is None and d["payload"] == {"x": 1}
    assert b["code"] == "KATEGORI_DIPAKAI_PELANGGAN" and b["jumlah_pelanggan"] == 7 and b["nama_pelanggan"] == list("ABCDE") and b["sisa_pelanggan"] == 2
    assert "A, B, C, D, E dan 2 lainnya" in b["message"] and "Warung dipakai 7 pelanggan" in b["message"]
    assert not _tulis(k) and k.tr == [("start", True), ("rollback", True)]


def test_boleh_saat_kosong_rencana_hapus_permanen(monkeypatch):
    k = KonT(n_semua=0)
    d = _app(monkeypatch, k).post(f"/api/customer-categories/{KID}/delete/preview").json()["data"]
    assert d["ok"] is True and d["blocks"] == [] and d["rencana"] == {"jenis": "hapus_permanen"} and d["payload"] == {}
    assert not _tulis(k)


def test_404_tak_ada_atau_lintas_tenant_dan_bersaring_tenant(monkeypatch):
    k = KonT(ada=False)
    r = _app(monkeypatch, k, tenant="t-lain").post(f"/api/customer-categories/{KID}/delete/preview")
    assert r.status_code == 404 and not _tulis(k)
    assert all("tenant_id = $" in q for q, _ in k.sql if "customer_categories" in q or "FROM customers" in q)
    assert [str(x) for x in k.sql[0][1]] == [KID, "t-lain"]


def test_pratinjau_dan_delete_sama_kalimat_dan_jumlah(monkeypatch):
    kw = dict(n_semua=9, n=8, nama=["Budi", "Sari", "Ani"])
    pr = _app(monkeypatch, KonT(**kw)).post(f"/api/customer-categories/{KID}/delete/preview").json()["data"]["blocks"][0]
    de = _app(monkeypatch, KonT(**kw)).delete(f"/api/customer-categories/{KID}")
    assert de.status_code == 409
    j = de.json()
    for f in ("code", "message", "jumlah_pelanggan", "nama_pelanggan", "sisa_pelanggan"):
        assert j[f] == pr[f], f


def test_satu_penentu_tanpa_duplikat():
    src = Path(KP.__file__).read_text()
    assert src.count("KATEGORI_DIPAKAI_PELANGGAN") == 1
    assert "rencana_hapus" in Path(CC.__file__).read_text() and "KATEGORI_DIPAKAI_PELANGGAN" not in Path(CC.__file__).read_text()
