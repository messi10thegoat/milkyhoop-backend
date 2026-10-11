"""11 Okt 2026 (MASTER via WORKSPACE): (1) 409 kode/SKU barang dikirim berkode {code: KODE_BARANG_DIPAKAI, message,
terhapus}, pesan teks tetap, tanpa nama/id pemegang; (2) PATCH /items/{id}/status tak menyentuh baris deleted_at terisi
(404, bukan 'aktif tapi tersembunyi'). Kontrol: tanpa pemegang kode, alur lolos pemeriksaan SKU; baris tak terhapus, UPDATE jalan."""
import asyncio
import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.routers import items as I
from app.schemas.items import CreateItemRequest as C

T = "tenant-uji"
REQ = SimpleNamespace(state=SimpleNamespace(user={"tenant_id": T, "user_id": "u-1"}))


class Lewat(Exception):
    """Tanda: alur lolos SEMUA pemeriksaan duplikat dan sampai ke langkah berikutnya."""


class Conn:
    def __init__(self, pemegang=None):
        self.pemegang, self.q = pemegang, []

    async def fetchrow(self, sql, *a):
        self.q.append(" ".join(sql.split()))
        if "sku = $2" in sql:
            return self.pemegang
        return None

    async def close(self):
        pass


def _jalan(monkeypatch, conn):
    async def sambung():
        return conn

    async def berhenti(*a, **k):
        raise Lewat()

    monkeypatch.setattr(I, "get_db_connection", sambung)
    monkeypatch.setattr(I, "_auto_default_product_accounts", berhenti)
    body = C(name="Kaos Uji", base_unit="pcs", item_type="goods", item_code="BRG-9001", sku="SKU-9001")
    return asyncio.run(I.create_item(REQ, body))


@pytest.mark.parametrize("terhapus", [True, False])
def test_sku_dipakai_409_berkode_membawa_terhapus(monkeypatch, terhapus):
    conn = Conn({"id": uuid.uuid4(), "terhapus": terhapus})
    with pytest.raises(HTTPException) as e:
        _jalan(monkeypatch, conn)
    assert e.value.status_code == 409
    assert e.value.detail == {"code": "KODE_BARANG_DIPAKAI", "message": "Kode item SKU-9001 sudah digunakan",
                              "terhapus": terhapus}
    assert set(e.value.detail) == {"code", "message", "terhapus"}  # tanpa id/nama pemegang
    sql = [q for q in conn.q if "sku = $2" in q][0]
    assert "deleted_at" in sql  # baris terhapus ikut dibaca (indeks V072 mencakupnya)


def test_kontrol_tanpa_pemegang_kode_lolos_pemeriksaan_sku(monkeypatch):
    with pytest.raises(HTTPException) as e:  # create_item membungkus galat tak terduga jadi 500; sebabnya = tanda Lewat
        _jalan(monkeypatch, Conn(None))
    assert e.value.status_code == 500 and isinstance(e.value.__context__, Lewat)  # bukan 409: pagar bisa MELOLOSKAN


# ---- PATCH status ----
class _Acq:
    def __init__(self, c): self.c = c
    async def __aenter__(self): return self.c
    async def __aexit__(self, *a): return False


class _Pool:
    def __init__(self, hasil):
        self.hasil, self.sql = hasil, []

    def acquire(self): return _Acq(self)

    async def execute(self, sql, *a):
        self.sql.append(" ".join(sql.split()))
        return self.hasil


def _patch(monkeypatch, hasil):
    pool = _Pool(hasil)

    async def gp(): return pool

    async def js(): return {"is_active": True}

    monkeypatch.setattr(I, "get_pool", gp)
    req = SimpleNamespace(state=REQ.state, json=js)
    return pool, req


def test_status_menolak_baris_terhapus_404(monkeypatch):
    pool, req = _patch(monkeypatch, "UPDATE 0")  # baris deleted_at terisi tak cocok WHERE -> 0 baris
    with pytest.raises(HTTPException) as e:
        asyncio.run(I.update_item_status(req, str(uuid.uuid4())))
    assert e.value.status_code == 404
    assert "deleted_at IS NULL" in pool.sql[0] and "tenant_id = $3" in pool.sql[0]


def test_kontrol_status_baris_biasa_jalan(monkeypatch):
    pool, req = _patch(monkeypatch, "UPDATE 1")
    out = asyncio.run(I.update_item_status(req, str(uuid.uuid4())))
    assert out is not None and len(pool.sql) == 1
