"""BUG-338A (susulan, audit): response_model memangkas kunci yang tak dideklarasikan -> tiap field 338A yang lewat response_model dibuktikan LOLOS
lewat FastAPI sungguhan (TestClient + response_model rute asli), bukan lewat panggilan handler langsung."""
import typing
from typing import Any, Optional, get_args, get_origin
from uuid import UUID

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel
from pydantic_core import PydanticUndefined

from app.schemas.customers import CustomerDetailResponse, CustomerListResponse
from app.schemas.price_lists import PriceListDetailResponse


def _nilai(ann):
    o = get_origin(ann)
    if o is typing.Union:
        a = [x for x in get_args(ann) if x is not type(None)]
        return _nilai(a[0])
    if o in (list, typing.List):
        return []
    if o in (dict, typing.Dict) or ann is dict:
        return {}
    if isinstance(ann, type) and issubclass(ann, BaseModel):
        return _minimal(ann)
    if ann is bool:
        return True
    if ann is int:
        return 0
    if ann is float:
        return 0.0
    if ann is UUID:
        return "11111111-1111-1111-1111-111111111111"
    if o is typing.Literal:
        return get_args(ann)[0]
    return "x"


def _minimal(model) -> dict:
    return {k: _nilai(f.annotation) for k, f in model.model_fields.items() if f.default is PydanticUndefined and f.default_factory is None}


def _lewat(model, data: dict) -> dict:
    app = FastAPI()

    @app.get("/x", response_model=model)
    async def x():
        return {"success": True, "data": data}
    return TestClient(app).get("/x").json()["data"]


def _data(model, **tambah):
    d = _minimal(model.model_fields["data"].annotation)
    d.update(tambah)
    return d


def test_detail_dh_kategori_pemakai_hadir_untuk_kosong_dan_satu_kategori():
    k = [{"id": "k1", "name": "Warung", "is_active": True}]
    assert _lewat(PriceListDetailResponse, _data(PriceListDetailResponse, kategori_pemakai=k))["kategori_pemakai"] == k
    assert _lewat(PriceListDetailResponse, _data(PriceListDetailResponse, kategori_pemakai=[]))["kategori_pemakai"] == []


def test_detail_dh_tanpa_kunci_dari_handler_tetap_ada_default_kosong():
    assert _lewat(PriceListDetailResponse, _data(PriceListDetailResponse))["kategori_pemakai"] == []


def test_audit_detail_pelanggan_category_id_dan_name_lolos_response_model():
    d = _lewat(CustomerDetailResponse, _data(CustomerDetailResponse, category_id="k1", category_name="Warung"))
    assert (d["category_id"], d["category_name"]) == ("k1", "Warung")


def test_audit_daftar_pelanggan_category_id_dan_name_lolos_response_model():
    item = _minimal(CustomerListResponse.model_fields["items"].annotation.__args__[0])
    item.update(category_id="k1", category_name="Warung")
    app = FastAPI()

    @app.get("/x", response_model=CustomerListResponse)
    async def x():
        return {"items": [item], "total": 1, "has_more": False}
    d = TestClient(app).get("/x").json()
    assert (d["items"][0]["category_id"], d["items"][0]["category_name"]) == ("k1", "Warung")


def test_audit_badan_post_patch_pelanggan_menerima_category_id_tidak_dibuang_diam():
    from app.schemas.customers import CreateCustomerRequest, UpdateCustomerRequest
    kid = "22222222-2222-2222-2222-222222222222"
    assert str(UpdateCustomerRequest.model_validate({"category_id": kid}).category_id) == kid
    assert "category_id" in UpdateCustomerRequest.model_validate({"category_id": None}).model_fields_set
    assert "category_id" in CreateCustomerRequest.model_fields


# --- rute ASLI (router + handler galat main.py) dgn koneksi palsu: blok 409 hapus lewat DELETE / preview / hapus kategori -----------------------
import contextlib  # noqa: E402

from fastapi.responses import JSONResponse  # noqa: E402

from app.routers import customer_categories as RC  # noqa: E402
from app.routers import price_lists as RP  # noqa: E402
from app.services.galat_berkode import GalatBerkode  # noqa: E402

PL = "33333333-3333-3333-3333-333333333333"
KT = "44444444-4444-4444-4444-444444444444"


class _Tr:
    def __init__(self, *a, **k):
        pass

    async def start(self):
        pass

    async def rollback(self):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class _Konek:
    def __init__(self, kategori=(), pelanggan_semua=0, pelanggan_nama=()):
        self.kategori, self.pelanggan_semua, self.pelanggan_nama, self.sql = list(kategori), pelanggan_semua, list(pelanggan_nama), []

    def transaction(self, **k):
        return _Tr()

    async def fetchrow(self, sql, *a):
        q = " ".join(sql.split()); self.sql.append(q)
        if "FROM price_lists" in q:
            return {"id": PL, "code": "DH1", "name": "Grosir", "is_active": True, "is_default": False, "start_date": None, "end_date": None, "priority": 100, "description": None}
        if "FROM customer_categories" in q:
            return {"name": "Warung"}
        return None

    async def fetchval(self, sql, *a):
        q = " ".join(sql.split()); self.sql.append(q)
        if "COUNT(*) FROM customers" in q:
            return self.pelanggan_semua if "deleted_at" not in q else len(self.pelanggan_nama)
        return 0

    async def fetch(self, sql, *a):
        q = " ".join(sql.split())
        if "FROM customer_categories" in q:
            return list(self.kategori)
        if "FROM customers" in q:
            return [{"nama": n} for n in self.pelanggan_nama]
        return []

    async def execute(self, sql, *a):
        self.sql.append(" ".join(sql.split()))


def _app(monkeypatch, konek):
    class Pool:
        @contextlib.asynccontextmanager
        async def acquire(self):
            yield konek

    async def pool():
        return Pool()
    monkeypatch.setattr(RP, "get_pool", pool)
    monkeypatch.setattr(RC, "get_pool", pool)
    app = FastAPI()

    @app.exception_handler(GalatBerkode)
    async def _h(request, exc):
        return JSONResponse(status_code=exc.status_code, content=exc.badan())

    @app.middleware("http")
    async def _u(request, call_next):
        request.state.user = {"tenant_id": "t-1", "user_id": "55555555-5555-5555-5555-555555555555"}
        return await call_next(request)
    app.include_router(RP.router, prefix="/api/price-lists")
    app.include_router(RC.router, prefix="/api/customer-categories")
    return TestClient(app)


KAT = [{"id": KT, "name": "Warung", "is_active": True}, {"id": "k2", "name": "Toko", "is_active": False}]


def test_audit_delete_dh_dipakai_kategori_409_membawa_jumlah_dan_nama(monkeypatch):
    k = _Konek(kategori=KAT)
    r = _app(monkeypatch, k).delete(f"/api/price-lists/{PL}")
    b = r.json()
    code = b.get("code") or (b.get("detail") or {}).get("code") if isinstance(b.get("detail"), dict) else b.get("code")
    assert r.status_code == 409 and code == "DH_DIPAKAI_KATEGORI", b
    assert b["message"].count("kategori pelanggan") == 1 and "Warung, Toko" in b["message"]
    assert b["jumlah_kategori"] == 2 and b["nama_kategori"] == ["Warung", "Toko"] and not any(q.startswith("DELETE") for q in k.sql)


def test_audit_preview_hapus_dh_dipakai_kategori_blok_lolos_ke_respons(monkeypatch):
    r = _app(monkeypatch, _Konek(kategori=KAT)).post(f"/api/price-lists/{PL}/delete/preview", json={})
    bl = r.json()["data"]["blocks"][0]
    assert r.status_code == 200 and bl["code"] == "DH_DIPAKAI_KATEGORI" and bl["jumlah_kategori"] == 2 and bl["nama_kategori"] == ["Warung", "Toko"]


def test_audit_hapus_kategori_masih_ada_pelanggan_409_membawa_jumlah_dan_nama(monkeypatch):
    k = _Konek(pelanggan_semua=3, pelanggan_nama=["Budi", "Sari"])
    r = _app(monkeypatch, k).delete(f"/api/customer-categories/{KT}")
    b = r.json()
    assert r.status_code == 409 and b["code"] == "KATEGORI_DIPAKAI_PELANGGAN" and b["jumlah_pelanggan"] == 3 and b["nama_pelanggan"] == ["Budi", "Sari"]
    assert "dipakai 3 pelanggan" in b["message"] and not any(q.startswith("DELETE") for q in k.sql)
