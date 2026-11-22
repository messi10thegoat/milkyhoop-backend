"""BUG-338A: services/kategori_pelanggan (CRUD + 409 hapus), HA.rencana_hapus (kategori), migrasi V508 (FK komposit tenant, RLS, rollback)."""
import asyncio
import re
from pathlib import Path

import pytest

from app.services import harga_aksi as HA
from app.services import kategori_pelanggan as KP
from app.services.galat_berkode import GalatBerkode

MIG = Path(__file__).resolve().parents[3] / "migrations"
T = "t-1"


class Kon:
    def __init__(self, ada=True, n_semua=0, n=0, nama=(), sah_dh=True, sah_kat=True):
        self.ada, self.n_semua, self.n, self.nama, self.sah_dh, self.sah_kat = ada, n_semua, n, list(nama), sah_dh, sah_kat
        self.sql = []

    async def fetchval(self, sql, *a):
        q = " ".join(sql.split()); self.sql.append((q, a))
        if "FROM price_lists" in q:
            return 1 if self.sah_dh else None
        if "FROM customer_categories" in q and q.startswith("SELECT 1"):
            return 1 if self.sah_kat else None
        if "COUNT(*)" in q:
            return self.n_semua if "deleted_at" not in q else self.n
        return None

    async def fetchrow(self, sql, *a):
        self.sql.append((" ".join(sql.split()), a))
        return {"name": "Warung"} if self.ada else None

    async def fetch(self, sql, *a):
        return [{"nama": x} for x in self.nama]

    async def execute(self, sql, *a):
        self.sql.append((" ".join(sql.split()), a))


def galat(coro):
    with pytest.raises(GalatBerkode) as e:
        asyncio.run(coro)
    return e.value


def test_hapus_kategori_dipakai_409_menyebut_jumlah_nama_dan_tidak_menghapus():
    k = Kon(n_semua=3, n=2, nama=["Budi", "Sari"])
    e = galat(KP.hapus(k, T, "k1"))
    b = e.badan()
    assert e.status_code == 409 and b["code"] == "KATEGORI_DIPAKAI_PELANGGAN" and b["jumlah_pelanggan"] == 3 and b["nama_pelanggan"] == ["Budi", "Sari"]
    assert "dipakai 3 pelanggan" in b["message"] and not any(q.startswith("DELETE") for q, _ in k.sql)


def test_hapus_kategori_bebas_menghapus_bersaring_tenant_dan_tak_ada_404():
    k = Kon(n_semua=0)
    asyncio.run(KP.hapus(k, T, "k1"))
    q, a = k.sql[-1]
    assert q.startswith("DELETE FROM customer_categories") and "tenant_id = $2" in q and a == ("k1", T)
    assert galat(KP.hapus(Kon(ada=False), T, "k1")).status_code == 404


def test_buat_nama_kosong_422_dan_daftar_harga_asing_400():
    assert galat(KP.buat(Kon(), T, "   ", None)).badan()["code"] == "KATEGORI_NAMA_KOSONG"
    e = galat(KP.buat(Kon(sah_dh=False), T, "Grosir", "pl-lain-tenant"))
    assert e.status_code == 400 and e.badan()["code"] == "DH_TAK_ADA"


def test_pastikan_kategori_asing_atau_nonaktif_ditolak_none_lolos_dan_sql_bersaring_tenant():
    k = Kon(sah_kat=False)
    assert galat(KP.pastikan_kategori(k, T, "k-lain")).badan()["code"] == "KATEGORI_TAK_ADA"
    q, a = k.sql[0]
    assert "tenant_id = $2" in q and "is_active" in q and a == ("k-lain", T)
    asyncio.run(KP.pastikan_kategori(Kon(sah_kat=False), T, None))


def test_rencana_hapus_dh_dipakai_kategori_diblok_dengan_jumlah_dan_nama():
    r = {"name": "Grosir", "is_default": False}
    kat = [{"id": "1", "name": "Warung", "is_active": True}, {"id": "2", "name": "Toko", "is_active": False}]
    x = HA.rencana_hapus(r, 0, 4, (), kat)
    b = x["blocks"][0]
    assert not x["ok"] and b["code"] == "DH_DIPAKAI_KATEGORI" and b["jumlah_kategori"] == 2 and b["nama_kategori"] == ["Warung", "Toko"] and "2 kategori pelanggan" in b["message"]
    assert HA.rencana_hapus(r, 0, 4, (), [])["ok"] is True
    assert HA.rencana_hapus(r, 2, 4, ["A"], kat)["blocks"][0]["code"] == "DH_DIPAKAI_PELANGGAN"   # pelanggan didahulukan


def test_migrasi_v508_fk_komposit_tenant_rls_unik_nama_dan_rollback_menutup():
    s = " ".join((MIG / "V508__kategori_pelanggan.sql").read_text().split())
    assert "UNIQUE (tenant_id, id)" in s and "FOREIGN KEY (tenant_id, price_list_id) REFERENCES price_lists (tenant_id, id)" in s
    assert "FOREIGN KEY (tenant_id, category_id) REFERENCES customer_categories (tenant_id, id)" in s
    assert "ENABLE ROW LEVEL SECURITY" in s and "current_setting('app.tenant_id', true)" in s and "lower(btrim(name))" in s
    kode = " ".join(l for l in (MIG / "V508__kategori_pelanggan.sql").read_text().splitlines() if not l.lstrip().startswith("--"))
    assert not re.search(r"ON DELETE SET NULL", kode)  # SET NULL pada FK komposit akan meng-NULL tenant_id (NOT NULL)
    rb = (MIG / "V508__kategori_pelanggan_ROLLBACK.sql").read_text()
    for kata in ("DROP COLUMN IF EXISTS category_id", "DROP TABLE IF EXISTS customer_categories", "DROP CONSTRAINT IF EXISTS uq_price_lists_tenant_id"):
        assert kata in rb
