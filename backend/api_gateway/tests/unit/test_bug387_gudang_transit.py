"""BUG-387: barang dalam perjalanan (gudang sistem Transit) tampil sebagai baris tersendiri di daftar gudang (with_totals) dan stok_per_gudang,
sehingga Σ gudang + Transit = stok barang. Nilai = rumus SAMA dgn gudang lain (ROUND(saldo × WAC)), tanpa aturan nilai baru."""
import asyncio
from decimal import Decimal
from pathlib import Path

from app.services import gudang_daftar as GD
from app.services import gudang_transit as GT

ROUTER = (Path(__file__).parents[2] / "app" / "routers" / "warehouses.py").read_text()


class _C:
    def __init__(self, r):
        self.r, self.sql = r, []

    async def fetchrow(self, sql, *a):
        self.sql.append((sql, a))
        return self.r


def test_dalam_perjalanan_contoh_a5_transit5():
    c = _C({"qty": Decimal("5.0000"), "nilai": Decimal("216665"), "barang": 1})
    r = asyncio.run(GD.dalam_perjalanan(c, "t1"))
    assert r == {"is_transit": True, "nama_text": GT.LABEL, "jumlah_barang": 1, "qty": 5.0, "qty_text": "5", "nilai": 216665,
                 "nilai_text": "Rp 216.665", "teks": "1 barang dalam perjalanan senilai Rp 216.665"}
    assert c.sql[0][1] == ("t1",)


def test_kosong_none():
    assert asyncio.run(GD.dalam_perjalanan(_C({"qty": 0, "nilai": 0, "barang": 0}), "t1")) is None


def test_sql_ledger_gudang_sistem_rumus_wac_sama():
    s = GD.SQL_TRANSIT
    assert "w.is_system" in s and "il.tenant_id = $1" in s and "ROUND(x.saldo * get_weighted_average_cost($1, x.product_id), 0)" in s


def test_daftar_with_totals_mengirim_baris_transit_dan_jumlah_semua():
    assert "GD.dalam_perjalanan(conn, ctx[\"tenant_id\"])" in ROUTER and "dalam_perjalanan=transit, sum_nilai_semua=semua" in ROUTER


def test_stok_per_gudang_menandai_is_transit():
    assert '"is_transit": bool(r["is_system"])' in Path(GD.__file__).read_text()
