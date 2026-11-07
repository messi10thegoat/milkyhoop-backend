"""BUG-086: GET /items/{id} memberi tanda TERKUNCI -- conversions[].terpakai + base_unit_terpakai -- dari penentu YANG SAMA dengan
penolak PUT (satuan_dasar.konversi_terpakai / dasar_terpakai), supaya FE mengunci input alih-alih membiarkan ketik lalu 409."""
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routers import items as RI
from app.schemas import items as S
from app.services import satuan_dasar as SD
from app.services.galat_berkode import GalatBerkode

PID = str(uuid.uuid4())


class _Baris(dict):
    def __missing__(self, k):
        return None


class _Conn:
    async def fetchrow(self, sql, *a):
        return _Baris(id=PID, nama_produk="Minyak", satuan="botol", base_unit="botol", item_type="goods", track_inventory=True,
                      current_stock=0, stock_value=None, transaction_count=0)

    async def fetch(self, sql, *a):
        if "unit_conversions" in sql:
            return [_Baris(id="c1", base_unit="botol", conversion_unit="Dus", conversion_factor=12, is_active=True),
                    _Baris(id="c2", base_unit="botol", conversion_unit="pak", conversion_factor=6, is_active=True)]
        return []

    async def fetchval(self, sql, *a):
        return None

    async def close(self):
        pass


def _klien(monkeypatch, dipakai, dasar):
    async def conn():
        return _Conn()

    async def konv(c, tid, pid, satuan):
        return satuan in dipakai

    async def das(c, tid, pid):
        return dasar
    monkeypatch.setattr(RI, "get_db_connection", conn)
    monkeypatch.setattr(SD, "konversi_terpakai", konv)
    monkeypatch.setattr(SD, "dasar_terpakai", das)
    app = FastAPI()

    @app.middleware("http")
    async def masuk(request, call_next):
        request.state.user = {"tenant_id": "t1", "user_id": "00000000-0000-0000-0000-000000000001"}
        return await call_next(request)
    app.include_router(RI.router, prefix="/api")
    return TestClient(app)


def test_get_item_tanda_terpakai_per_konversi_dan_dasar(monkeypatch):
    r = _klien(monkeypatch, {"Dus"}, True).get(f"/api/items/{PID}")
    assert r.status_code == 200, r.text
    d = r.json()["data"]
    assert {c["conversion_unit"]: c["terpakai"] for c in d["conversions"]} == {"Dus": True, "pak": False}
    assert d["base_unit_terpakai"] is True


def test_get_item_belum_terpakai(monkeypatch):
    d = _klien(monkeypatch, set(), False).get(f"/api/items/{PID}").json()["data"]
    assert [c["terpakai"] for c in d["conversions"]] == [False, False] and d["base_unit_terpakai"] is False


def test_skema_konversi_mendeklarasikan_terpakai():
    assert "terpakai" in S.UnitConversionResponse.model_fields


@pytest.mark.asyncio
async def test_penolak_dasar_memakai_penentu_yang_sama(monkeypatch):
    """SATU sumber: tolak_ubah_dasar memutus lewat dasar_terpakai (yang juga mengisi tanda GET)."""
    async def ya(c, tid, pid):
        return True

    async def alias(c, tid):
        return {}
    monkeypatch.setattr(SD, "dasar_terpakai", ya)
    monkeypatch.setattr(SD, "_alias", alias)
    with pytest.raises(GalatBerkode):
        await SD.tolak_ubah_dasar(_Conn(), "t1", PID, "botol", "liter")
