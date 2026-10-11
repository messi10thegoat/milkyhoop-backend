"""11 Okt 2026 (MASTER): nama barang ganda (sesama barang belum dihapus) -> 409 detail {code: NAMA_BARANG_DIPAKAI, message}
di create DAN update; balapan yang ditolak indeks V402 idx_products_tenant_nama -> 409 yang sama, bukan 500.
Pesan wajib terbaca FE r227 sebagai galat medan NAMA (barang.ts petakanGalatBarang + useDataForm petakanGalatItem
memilih medan dari isi pesan)."""
import asyncio
import re
import uuid
from types import SimpleNamespace

import asyncpg
import pytest
from fastapi import HTTPException

from app.routers import items as I
from app.schemas.items import CreateItemRequest as C, UpdateItemRequest as U

T = "tenant-uji"
REQ = SimpleNamespace(state=SimpleNamespace(user={"tenant_id": T, "user_id": "u-1"}))


class Conn:
    """fetchrow: barang ada / nama dipakai (`ada`), atau melempar `galat` di panggilan pertama (DB menolak)."""

    def __init__(self, ada=True, galat=None):
        self.ada, self.galat, self.q, self.ditutup = ada, galat, [], False

    async def fetchrow(self, sql, *a):
        self.q.append(sql)
        if self.galat is not None:
            raise self.galat
        return {"id": "lain"} if self.ada else None

    async def close(self):
        self.ditutup = True


def _pasang(monkeypatch, conn):
    async def sambung():
        return conn
    monkeypatch.setattr(I, "get_db_connection", sambung)


def _galat(coro):
    with pytest.raises(HTTPException) as e:
        asyncio.run(coro)
    return e.value


def _unik(constraint):
    e = asyncpg.UniqueViolationError("duplicate key value violates unique constraint")
    e.constraint_name = constraint
    return e


def _berkode(e):
    assert e.status_code == 409
    assert e.detail == {"code": "NAMA_BARANG_DIPAKAI", "message": I.PESAN_NAMA_BARANG}


def test_create_nama_dipakai_409_berkode(monkeypatch):
    conn = Conn(ada=True)
    _pasang(monkeypatch, conn)
    _berkode(_galat(I.create_item(REQ, C(name="Kaos Polos", base_unit="pcs", item_type="goods"))))
    assert "nama_produk = $2" in conn.q[0] and "deleted_at IS NULL" in conn.q[0]
    assert conn.ditutup


def test_update_rename_ke_nama_dipakai_409_berkode(monkeypatch):
    conn = Conn(ada=True)  # panggilan 1: barang ada; panggilan 2: nama dipakai barang lain
    _pasang(monkeypatch, conn)
    _berkode(_galat(I.update_item(REQ, uuid.uuid4(), U(name="Kaos Polos"))))
    assert "id != $3" in conn.q[1] and "deleted_at IS NULL" in conn.q[1]


@pytest.mark.parametrize("panggil", ["create", "update"])
def test_balapan_indeks_nama_jadi_409_bukan_500(monkeypatch, panggil):
    _pasang(monkeypatch, Conn(galat=_unik("idx_products_tenant_nama")))
    co = (I.create_item(REQ, C(name="Kaos Polos", base_unit="pcs", item_type="goods")) if panggil == "create"
          else I.update_item(REQ, uuid.uuid4(), U(name="Kaos Polos")))
    _berkode(_galat(co))


def test_bentrok_indeks_lain_tetap_500_tak_disamarkan(monkeypatch):
    _pasang(monkeypatch, Conn(galat=_unik("idx_products_tenant_sku")))
    e = _galat(I.create_item(REQ, C(name="Kaos Polos", base_unit="pcs", item_type="goods")))
    assert e.status_code == 500


def test_nama_barang_bentrok_hanya_unique_violation_indeks_nama():
    assert I.nama_barang_bentrok(_unik("idx_products_tenant_nama"))
    assert not I.nama_barang_bentrok(_unik("idx_products_tenant_sku"))
    assert not I.nama_barang_bentrok(ValueError("idx_products_tenant_nama"))


def test_pesan_terbaca_fe_sebagai_medan_nama():
    """Cermin pemeta FE r227: kata berikut memindahkan galat 409 ke medan lain -> dilarang di pesan."""
    m = I.PESAN_NAMA_BARANG
    assert not re.search(r"barcode|code|kode|sku", m, re.I)          # barang.ts -> barcode/kode
    assert not re.search(r"base_unit|satuan|\bunit\b|sales_price|harga|price", m, re.I)  # useDataForm -> satuan/harga
    assert re.search(r"\bname\b|nama", m, re.I)                       # useDataForm -> nama
    assert "{" not in m and '"' not in m                              # tanpa nama barang (bisa memuat kata di atas)


def test_tak_ada_lagi_string_lama():
    import inspect
    src = inspect.getsource(I)
    assert "Item with this name already exists" not in src
    assert src.count("raise galat_nama_barang_dipakai()") == 4  # create, update, 2x balapan
