"""Seragam: galat berkode Daftar Harga (D3 ubah massal, salin, riwayat, impor) = code + message + field di TINGKAT ATAS (GalatBerkode), bukan objek di dalam `detail`."""
import asyncio
import inspect
from uuid import uuid4

import asyncpg
import pytest
from starlette.requests import Request

from app.routers import bulk_price_lists as BP
from app.routers import price_lists as PL
from app.services import harga_salin as HS
from app.services import idem_buat
from app.services.galat_berkode import GalatBerkode


def req(kunci=None):
    h = [(b"x-idempotency-key", kunci.encode())] if kunci else []
    r = Request({"type": "http", "method": "POST", "path": "/api/price-lists", "headers": h})
    r.state.user = {"tenant_id": "t-1", "user_id": "00000000-0000-0000-0000-000000000001", "username": "u"}
    return r


def galat(coro):
    with pytest.raises(GalatBerkode) as e:
        asyncio.run(coro)
    return e.value


def test_bulk_kunci_wajib_top_level_400():
    e = galat(BP.bulk_price(req(), uuid4(), BP.BulkHargaRequest(mode="persen", nilai=10)))
    assert (e.status_code, e.code) == (400, "BULK_KUNCI_WAJIB") and e.badan() == {"detail": e.detail, "code": "BULK_KUNCI_WAJIB", "message": e.detail}
    assert "X-Idempotency-Key" in e.detail


def test_salin_kunci_wajib_top_level_400():
    e = galat(BP.salin_daftar(req(), uuid4(), {"code": "A", "name": "B"}))
    assert (e.status_code, e.code) == (400, "SALIN_KUNCI_WAJIB") and e.badan()["code"] == "SALIN_KUNCI_WAJIB" and e.badan()["message"] == e.detail


def test_impor_kunci_wajib_top_level_400():
    e = galat(PL.impor_harga(req(), uuid4(), {"baris": []}))
    assert (e.status_code, e.code) == (400, "IMPOR_KUNCI_WAJIB") and e.badan()["code"] == "IMPOR_KUNCI_WAJIB"


def test_riwayat_aksi_tak_dikenal_top_level_400_dengan_field():
    e = galat(BP.riwayat_daftar(req(), uuid4(), limit=50, offset=0, aksi="NGAWUR", dari=None, sampai=None))
    assert (e.status_code, e.code, e.field) == (400, "RIWAYAT_AKSI_TIDAK_DIKENAL", "aksi") and e.badan()["field"] == "aksi"


class Tx:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class Konn:
    def transaction(self):
        return Tx()


class Pool:
    def acquire(self):
        class A:
            async def __aenter__(s):
                return Konn()

            async def __aexit__(s, *a):
                return False
        return A()


def test_salin_balapan_kode_unique_violation_jadi_409_top_level(monkeypatch):
    async def pool():
        return Pool()

    async def mulai(conn, ctx, kunci, prefix, doc_id, isi, response=None):
        return None, None, None

    async def tulis(conn, ctx, sumber, kode, nama):
        raise asyncpg.exceptions.UniqueViolationError("duplicate key uq_price_lists_tenant_code")
    monkeypatch.setattr(BP, "get_pool", pool)
    monkeypatch.setattr(idem_buat, "mulai_aksi", mulai)
    monkeypatch.setattr(HS, "tulis", tulis)
    e = galat(BP.salin_daftar(req("k-1"), uuid4(), {"code": "A", "name": "B"}))
    assert (e.status_code, e.code, e.field) == (409, "KODE_DAFTAR_GANDA", "code") and e.badan() == {"detail": e.detail, "code": "KODE_DAFTAR_GANDA", "message": e.detail, "field": "code"}


def test_tak_ada_lagi_galat_objek_di_dalam_detail_pada_rute_publik_bulk_price_lists():
    s = inspect.getsource(BP)
    publik = [BP.bulk_price, BP.salin_daftar, BP.riwayat_daftar, BP.pratinjau_salin, BP.bulk_price_preview]
    for f in publik:
        assert 'detail={"code"' not in inspect.getsource(f), f.__name__
    assert 'detail={"code": r["blocks"][0]["code"]' in s          # SATU pengecualian disengaja: galat per-item di dalam fn massal (dibaca bulk._pesan sebagai dict)
