"""BUG-340 (putusan pemilik 22 Nov): pelanggan terhapus tak dihitung di DH (daftar, detail, blok hapus/status, nama di pesan); nonaktif tetap dihitung;
pelanggan[] tetap memuat terhapus + pelanggan_total_termasuk_terhapus (aditif)."""
import asyncio
import inspect

from app.routers import price_lists as PL
from app.services import harga_aksi as HA


class Konek:
    def __init__(self, nilai=None, rows=None):
        self.nilai, self.rows, self.sql = nilai, rows or [], []

    async def fetchval(self, sql, *a):
        self.sql.append(sql)
        return self.nilai

    async def fetch(self, sql, *a):
        self.sql.append(sql)
        return self.rows


def test_ukur_pelanggan_tanpa_terhapus_nonaktif_tetap_dihitung():
    k = Konek(3)
    assert asyncio.run(HA.ukur_pelanggan(k, "t", "pl")) == 3
    s = " ".join(k.sql[0].split())
    assert "deleted_at IS NULL" in s and "is_active" not in s


def test_nama_pelanggan_tanpa_terhapus():
    k = Konek(rows=[{"nama": "Andi"}])
    assert asyncio.run(HA.ukur_nama_pelanggan(k, "t", "pl")) == ["Andi"]
    assert "deleted_at IS NULL" in k.sql[0]


def test_daftar_dan_detail_hitung_tanpa_terhapus():
    assert "c.deleted_at IS NULL" in inspect.getsource(PL.list_price_lists)
    assert "deleted_at IS NULL" in inspect.getsource(PL.get_price_list).split("n_pel = ")[1].split("\n")[0]


def test_hanya_terhapus_memakai_daftar_tak_memblok_hapus():
    # hitung 0 (terhapus tak dihitung) -> rencana_hapus lolos
    r = {"name": "UJI", "is_active": True, "is_default": False}
    assert HA.rencana_hapus(r, 0, 2, [])["ok"] is True
    assert HA.rencana_hapus(r, 1, 2, ["Andi"])["ok"] is False


def test_pelanggan_terpotong_dan_total_termasuk_terhapus_dari_baris_penuh():
    rows = [{"id": f"00000000-0000-0000-0000-{i:012d}", "nama": f"P{i}", "nomor_member": None, "terhapus": i % 2 == 0, "total_semua": 51} for i in range(50)]
    h = asyncio.run(HA.daftar_pelanggan(Konek(rows=rows), "t", "pl"))
    assert h["pelanggan_total_termasuk_terhapus"] == 51 and h["pelanggan_terpotong"] is True
    assert h["pelanggan"][0]["terhapus"] is True and h["pelanggan"][1]["terhapus"] is False


def test_kueri_daftar_satu_agregat_window():
    k = Konek(rows=[])
    asyncio.run(HA.daftar_pelanggan(k, "t", "pl"))
    assert len(k.sql) == 1 and "COUNT(*) OVER ()" in k.sql[0]
