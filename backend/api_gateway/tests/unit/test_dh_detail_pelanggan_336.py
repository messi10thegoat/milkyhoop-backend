"""BUG-336: detail Daftar Harga membawa pelanggan[] (id, nama, kode, terhapus) maks 50 urut nama; predikat SAMA dgn pelanggan_count (tak menyaring deleted_at)."""
import asyncio
from datetime import datetime

from app.schemas.price_lists import PriceListDetail
from app.services import harga_aksi as HA


class Konek:
    def __init__(self, rows):
        self.rows, self.sql, self.args = rows, None, None

    async def fetch(self, sql, *args):
        self.sql, self.args = sql, args
        return self.rows


def r(i, nama, kode=None, hapus=False):
    return {"id": f"00000000-0000-0000-0000-{i:012d}", "nama": nama, "nomor_member": kode, "terhapus": hapus}


def jalan(rows, n):
    k = Konek(rows)
    return k, asyncio.run(HA.daftar_pelanggan(k, "t-1", "pl-1", n))


def test_bentuk_dan_terhapus_true_false():
    k, h = jalan([r(1, "Andi", "C-1"), r(2, "Budi", None, True)], 2)
    assert h["pelanggan"] == [{"id": "00000000-0000-0000-0000-000000000001", "nama": "Andi", "kode": "C-1", "terhapus": False},
                              {"id": "00000000-0000-0000-0000-000000000002", "nama": "Budi", "kode": None, "terhapus": True}]
    assert h["pelanggan_terpotong"] is False


def test_daftar_kosong():
    _, h = jalan([], 0)
    assert h == {"pelanggan": [], "pelanggan_terpotong": False}


def test_terpotong_hanya_bila_total_lebih_dari_50():
    assert jalan([r(i, f"P{i}") for i in range(50)], 50)[1]["pelanggan_terpotong"] is False
    assert jalan([r(i, f"P{i}") for i in range(50)], 51)[1]["pelanggan_terpotong"] is True


def test_sql_urut_stabil_limit_50_predikat_sama_hitung_tanpa_saring_terhapus():
    k, _ = jalan([], 0)
    s = " ".join(k.sql.split())
    assert "ORDER BY lower(nama), id LIMIT 50" in s
    assert "WHERE tenant_id = $1 AND price_list_id = $2" in s and "deleted_at IS NULL" not in s and "is_active" not in s
    assert k.args == ("t-1", "pl-1")


def test_skema_detail_membawa_medan_dengan_bawaan_kosong():
    assert PriceListDetail.model_fields["pelanggan"].default == [] and PriceListDetail.model_fields["pelanggan_terpotong"].default is False
