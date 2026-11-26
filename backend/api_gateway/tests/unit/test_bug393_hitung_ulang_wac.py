"""BUG-393: hitung ulang snapshot average_cost dari kartu (rumus SAMA hitung_wac_sesudah). Kasus nyata kaos (ukur 26 Nov, read-only)."""
import asyncio
from decimal import Decimal as D
from pathlib import Path

from app.services import hitung_ulang_wac as H

SRC = (Path(__file__).parents[2] / "app" / "services" / "hitung_ulang_wac.py").read_text()


def k(i, masuk, keluar, uc, ac):
    return {"id": i, "quantity_in": D(masuk), "quantity_out": D(keluar), "unit_cost": D(uc), "average_cost": D(ac)}


def test_void_penerimaan_pra_268b_dipulihkan():
    # Cotton Combed Maroon (kaos): 1 @43.069 sisa, GR 1 @1.000 lalu GR_VOID menyimpan 22.034,50 -> harus kembali 43.069
    kartu = [k(1, "101", "0", "43069", "43069"), k(2, "0", "100", "43069", "43069"), k(3, "1", "0", "1000", "22034.50"), k(4, "0", "1", "1000", "22034.50")]
    assert H.rencana(kartu) == [(4, D("22034.50"), D("43069.00"))]


def test_void_faktur_beli_kaos_hitam():
    kartu = [k(1, "3.25", "0", "128000", "128000"), k(2, "1", "0", "10000", "100235.29"), k(3, "0", "1", "10000", "100235.29")]
    assert H.rencana(kartu) == [(3, D("100235.29"), D("128000.00"))]


def test_kartu_benar_tanpa_perubahan_dan_stok_habis_pakai_cadangan():
    kartu = [k(1, "10", "0", "100", "100"), k(2, "10", "0", "200", "150"), k(3, "0", "20", "150", "150")]
    assert H.rencana(kartu) == []


def test_hanya_kolom_turunan_dan_tenant():
    assert "SET average_cost = $3 WHERE tenant_id = $1 AND id = $2" in H.SQL_UBAH and "tenant_id = $1" in H.SQL_KARTU
    assert "ORDER BY created_at, id" in H.SQL_KARTU and "quantity_in =" not in SRC and "journal" not in H.SQL_UBAH
    assert "hitung_wac_sesudah(" in SRC and "fifo_aktif" in SRC


class _C:
    def __init__(self, kartu): self.kartu, self.ubah = kartu, []
    async def fetchval(self, sql, *a): return False
    async def fetch(self, sql, *a): return self.kartu
    async def execute(self, sql, *a):
        if sql == H.SQL_UBAH:
            self.ubah.append(a)


def test_uji_kering_tak_menulis_terapkan_menulis(monkeypatch):
    from app.services import fifo_lapisan
    async def mati(*a): return False
    monkeypatch.setattr(fifo_lapisan, "fifo_aktif", mati)
    kartu = [k(1, "1", "0", "10", "10"), k(2, "0", "1", "10", "99")]
    c = _C(kartu)
    assert asyncio.run(H.hitung_ulang(c, "t", "p")) == [(2, D("99"), D("10"))] and c.ubah == []
    asyncio.run(H.hitung_ulang(c, "t", "p", terapkan=True))
    assert c.ubah == [("t", 2, D("10"))]
