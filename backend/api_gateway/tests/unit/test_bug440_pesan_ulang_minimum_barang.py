"""BUG-440 (ikut Accurate): Pesan ulang per gudang memakai minimum gudang bila ada, selain itu minimum BARANG (reorder_level) sekali di gudang utama."""
import asyncio
from decimal import Decimal

from app.services import stok_minimum_gudang as SMG


class Kon:
    def __init__(self, gudang, barang):
        self.g, self.b = gudang, barang

    async def fetch(self, sql, *a):
        return self.b if "reorder_level AS minimum" in sql else self.g


def r(id_, gid, gudang, minimum, stok):
    return {"id": id_, "nama_produk": f"Barang {id_}", "unit": "pcs", "gudang_id": gid, "gudang": gudang, "minimum": Decimal(minimum), "stok": Decimal(stok)}


def test_minimum_barang_ikut_sekali_di_gudang_utama():
    d = asyncio.run(SMG.daftar_pesan_ulang(Kon([r("a", "g2", "Cabang", 10, 4)], [r("b", "g1", "Utama", 200, 103)]), "t"))
    ids = [(x["id"], x["gudang_id"]) for x in d["items"]]
    assert ("a", "g2") in ids and ("b", "g1") in ids and len(ids) == 2
    b = next(x for x in d["items"] if x["id"] == "b")
    assert b["usulan_beli"] == Decimal("97") and b["sumber_minimum"] == "barang" and "semua gudang" in b["teks"]


def test_sql_minimum_barang_tak_dobel_dan_mengalah_ke_minimum_gudang():
    assert "NOT EXISTS (SELECT 1 FROM stok_minimum_gudang" in SMG._SQL_PESAN_BARANG and "LIMIT 1" in SMG._SQL_PESAN_BARANG
