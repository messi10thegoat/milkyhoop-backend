"""BUG-453: Pemindahan barang berlot -- alokasi batch FEFO/pilihan; satu gerak kartu per batch (Σ nilai == nilai baris); terima dari batch Transit."""
import asyncio
from decimal import Decimal
from pathlib import Path

from app.services import pemindahan_barang as PB
from app.services import pemindahan_dua_langkah as PD

APP = Path(__file__).resolve().parents[2] / "app"


def test_potong_batch_nilai_utuh():
    ln = {"quantity": Decimal("3"), "value": Decimal("1000.00"), "unit_cost": Decimal("333.333"),
          "batches": [{"batch_id": "b1", "quantity": Decimal("2")}, {"batch_id": "b2", "quantity": Decimal("1")}]}
    p = PB.potong_batch(ln)
    assert [(q, b) for q, _, b in p] == [(Decimal("2"), "b1"), (Decimal("1"), "b2")] and sum(v for _, v, _ in p) == Decimal("1000.00")
    assert PB.potong_batch({"quantity": Decimal("5"), "value": Decimal("50"), "batches": None}) == [(Decimal("5"), Decimal("50"), None)]


class Kon:
    def __init__(self, rows):
        self.rows = rows

    async def fetch(self, sql, *a):
        return self.rows


def test_potong_terima_dari_batch_transit():
    k = Kon([{"batch_id": "b1", "sisa": Decimal("2")}, {"batch_id": "b2", "sisa": Decimal("5")}])
    p = asyncio.run(PD.potong_terima(k, "t", "tr", "transit", "p", Decimal("3"), Decimal("300.00")))
    assert [(q, b) for q, _, b in p] == [(Decimal("2"), "b1"), (Decimal("1"), "b2")] and sum(v for _, v, _ in p) == Decimal("300.00")
    assert asyncio.run(PD.potong_terima(Kon([]), "t", "tr", "transit", "p", Decimal("3"), Decimal("9"))) == [(Decimal("3"), Decimal("9"), None)]


def test_tanpa_blok_belum_didukung_untuk_batch_dan_batch_ikut_penulis():
    s = (APP / "services/pemindahan_barang.py").read_text()
    assert "PB_BATCH_STOK_KURANG" in s and "if p[\"track_batches\"]:\n            blok(\"PB_BARANG_BATCH_SERIAL\"" not in s
    assert s.count("batch_id=bid") == 2 and 'batch_id=r.get("batch_id")' in s
    d = (APP / "services/pemindahan_dua_langkah.py").read_text()
    assert d.count("batch_id=bid") == 4 and 'batch_id=r.get("batch_id")' in d
