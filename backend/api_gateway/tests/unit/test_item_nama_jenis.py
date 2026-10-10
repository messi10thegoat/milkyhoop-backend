"""10 Okt 2026 (MASTER): B1 nama barang terhapus boleh dipakai ulang (V402 indeks parsial; dulu 500), B2 keunikan
pelanggan (409 {code,message}; V402), B3 bawaan panel Barang per tenant + non_inventory/service tanpa stok."""
import asyncio
import inspect
import pathlib
import re
import uuid

import pytest
from fastapi import HTTPException

from app.routers import items as I
from app.routers import customers as CU
from app.services import pelanggan_unik as PU
from app.schemas.items import CreateItemRequest as C

T = "tenant-uji"
MIG = pathlib.Path(I.__file__).resolve().parents[3] / "migrations"


def _j(c):
    return asyncio.run(c)


def _sql(nama):
    s = (MIG / nama).read_text()
    return re.sub(r"--[^\n]*", "", s)


# ---- B1: V402 ----
def test_v402_nama_barang_unik_hanya_yang_belum_dihapus():
    s = _sql("V402__indeks_nama_barang_nomor_pelanggan.sql")
    assert "DROP INDEX IF EXISTS idx_products_tenant_nama;" in s
    assert re.search(r"CREATE UNIQUE INDEX idx_products_tenant_nama ON products \(tenant_id, nama_produk\)\s+WHERE deleted_at IS NULL;", s)


def test_v402_pelanggan_indeks_mati_dihapus_nomor_dijaga():
    s = _sql("V402__indeks_nama_barang_nomor_pelanggan.sql")
    assert "ALTER TABLE customers DROP CONSTRAINT IF EXISTS uq_customers_tenant_name;" in s
    assert "DROP INDEX IF EXISTS idx_customers_tenant_code_unique;" in s
    assert re.search(r"CREATE UNIQUE INDEX uq_customers_tenant_nomor_member ON customers \(tenant_id, nomor_member\)\s+"
                     r"WHERE deleted_at IS NULL AND nomor_member IS NOT NULL;", s)
    r = _sql("V402__indeks_nama_barang_nomor_pelanggan_ROLLBACK.sql")
    assert "DROP INDEX IF EXISTS uq_customers_tenant_nomor_member;" in r and "UNIQUE (tenant_id, name)" in r
    assert "CREATE UNIQUE INDEX idx_products_tenant_nama ON products (tenant_id, nama_produk);" in r


def test_cek_nama_barang_app_sejajar_indeks_parsial():
    """App mengabaikan barang terhapus -- benar HANYA bersama indeks parsial V402 (sebelumnya: 500)."""
    for f in (I.create_item, I.update_item, I.duplicate_item):
        assert "nama_produk = $2" in inspect.getsource(f) and "deleted_at IS NULL" in inspect.getsource(f), f.__name__


# ---- B2 ----
class Conn:
    def __init__(self, ada=None, jenis=None):
        self.ada, self.jenis, self.q = ada, jenis or [], []

    async def fetchval(self, sql, *a):
        self.q.append((sql, a))
        return self.ada

    async def fetch(self, sql, *a):
        self.q.append((sql, a))
        return self.jenis


def test_nama_pelanggan_dipakai_409_berkode():
    with pytest.raises(HTTPException) as e:
        _j(PU.tolak_nama_dipakai(Conn(uuid.uuid4()), T, "Budi"))
    assert e.value.status_code == 409
    assert e.value.detail["code"] == "NAMA_PELANGGAN_DIPAKAI" and '"Budi"' in e.value.detail["message"]
    _j(PU.tolak_nama_dipakai(Conn(None), T, "Budi"))


def test_nomor_pelanggan_dipakai_409_berkode_predikat_sama_dengan_indeks():
    c, kid = Conn(uuid.uuid4()), uuid.uuid4()
    with pytest.raises(HTTPException) as e:
        _j(PU.tolak_nomor_dipakai(c, T, "C0007", kecuali_id=kid))
    assert e.value.status_code == 409 and e.value.detail["code"] == "KODE_PELANGGAN_DIPAKAI"
    sql, a = c.q[0]
    assert "nomor_member = $2 AND deleted_at IS NULL" in sql and "tenant_id = $1" in sql and a == (T, "C0007", str(kid))


def test_nama_pelanggan_hanya_aktif_dan_kosong_tak_bertanya():
    c = Conn(None)
    _j(PU.tolak_nama_dipakai(c, T, "X"))
    assert "is_active = true" in c.q[0][0]
    c = Conn(uuid.uuid4())
    _j(PU.tolak_nama_dipakai(c, T, None))
    _j(PU.tolak_nomor_dipakai(c, T, ""))
    assert c.q == []


def test_buat_ubah_pulihkan_pelanggan_lewat_pemeriksa():
    src = inspect.getsource(CU.create_customer)
    assert "tolak_nama_dipakai(conn, ctx[\"tenant_id\"], body.name)" in src
    assert "tolak_nomor_dipakai(conn, ctx[\"tenant_id\"], body.code)" in src
    assert "already exists" not in src
    src = inspect.getsource(CU.update_customer)
    assert "tolak_nomor_dipakai(conn, ctx[\"tenant_id\"], body.code, kecuali_id=customer_id)" in src
    assert src.index("tolak_nomor_dipakai(") < src.index("UPDATE customers")
    src = inspect.getsource(CU.reactivate_customer)
    assert "tolak_nomor_dipakai(conn, ctx[\"tenant_id\"], existing[\"nomor_member\"], kecuali_id=customer_id)" in src
    assert src.index("tolak_nomor_dipakai(") < src.index("UPDATE customers")


# ---- B3 ----
@pytest.mark.parametrize("hit,harap", [
    ({}, "goods"),
    ({"non_inventory": 93, "service": 11}, "non_inventory"),
    ({"goods": 21, "non_inventory": 6}, "goods"),
    ({"goods": 5, "non_inventory": 5}, "goods"),
    ({"non_inventory": 3, "service": 3}, "non_inventory"),
    ({"service": 4}, "service"),
])
def test_pilih_jenis_bawaan(hit, harap):
    assert I.pilih_jenis_bawaan(hit) == harap


@pytest.mark.parametrize("rows,jenis,kirim", [
    ([], "goods", False),
    ([{"item_type": "non_inventory", "n": 93, "kirim": 92}, {"item_type": "service", "n": 11, "kirim": 0}], "non_inventory", True),
    ([{"item_type": "goods", "n": 21, "kirim": 0}, {"item_type": "non_inventory", "n": 6, "kirim": 2}], "goods", False),
    ([{"item_type": "non_inventory", "n": 4, "kirim": 2}], "non_inventory", False),  # separuh = bukan mayoritas
    ([{"item_type": "goods", "n": 3, "kirim": 3}], "goods", False),  # bisa_dikirim goods tak dihitung
])
def test_bawaan_panel_barang(rows, jenis, kirim):
    c = Conn(jenis=rows)
    assert _j(I.bawaan_panel_barang(c, T)) == {"default_item_type": jenis, "default_bisa_dikirim": kirim}
    sql, a = c.q[0]
    assert "tenant_id = $1" in sql and "deleted_at IS NULL" in sql and a == (T,)


def test_default_accounts_memuat_bawaan_panel():
    assert "**(await bawaan_panel_barang(conn, tenant_id))" in inspect.getsource(I.get_default_accounts)


@pytest.mark.parametrize("jenis,harap", [("non_inventory", False), ("service", False), ("goods", True)])
def test_bukan_barang_stok_dipaksa_tanpa_stok(jenis, harap):
    b = C(name="X", base_unit="pcs", item_type=jenis)  # track_inventory TAK dikirim -> default skema True
    I.tanpa_stok_bila_bukan_barang(b)
    assert b.track_inventory is harap


def test_create_memaksa_tanpa_stok_sebelum_koneksi():
    src = inspect.getsource(I.create_item)
    assert src.index("tanpa_stok_bila_bukan_barang(body)") < src.index("get_db_connection()")
