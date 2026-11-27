"""BUG-438 (ikut Accurate): Terima PO / GRN menerima batch per baris (barang track_batches). Tanpa migrasi; HPP tetap (hanya lokasi batch)."""
import asyncio
import inspect
from datetime import date
from decimal import Decimal

from app.routers import purchase_orders as PO
from app.schemas.purchase_orders import ReceiveGoodsRequest
from app.services import grn_rencana as G


class Kon:
    def __init__(self, ada=None):
        self.ada = ada

    async def fetchrow(self, sql, *a):
        return self.ada


def nilai(track_batches=True, track_expiry=False, batch=None, ada=None, ber_stok=True, qty=Decimal("5")):
    baris = [{"po_item_id": "p1", "item_id": "i1", "nama": "Susu", "ber_stok": ber_stok, "qty": qty}]
    detail = {"p1": {"track_batches": track_batches, "track_expiry": track_expiry}}
    bloks = []
    asyncio.run(G._nilai_batch(Kon(ada), "t", baris, detail, {"p1": batch} if batch else {}, bloks))
    return baris[0], [b["code"] for b in bloks]


def test_batch_wajib_untuk_track_batches():
    assert nilai()[1] == ["BATCH_WAJIB"]


def test_batch_tidak_berlaku_untuk_barang_tanpa_lacak():
    assert nilai(track_batches=False, batch={"batch_number": "L1"})[1] == ["BATCH_TIDAK_BERLAKU"]
    assert nilai(track_batches=False)[1] == []


def test_batch_baru_dan_kedaluwarsa_wajib():
    b, kode = nilai(track_expiry=True, batch={"batch_number": "L9"})
    assert kode == ["BATCH_KEDALUWARSA_WAJIB"]
    b, kode = nilai(track_expiry=True, batch={"batch_number": " L9 ", "expiry_date": "2027-01-31"})
    assert kode == [] and b["batch"]["baru"] is True and b["batch"]["batch_number"] == "L9" and b["batch"]["expiry_date"] == date(2027, 1, 31)


def test_batch_lama_dipakai_dan_kedaluwarsa_beda_diblok():
    b, kode = nilai(batch={"batch_number": "L1"}, ada={"id": "B1", "expiry_date": date(2027, 1, 1)})
    assert kode == [] and b["batch"]["id"] == "B1" and b["batch"]["baru"] is False
    assert nilai(batch={"batch_number": "L1", "expiry_date": "2027-02-01"}, ada={"id": "B1", "expiry_date": date(2027, 1, 1)})[1] == ["BATCH_KEDALUWARSA_BEDA"]


def test_baris_hanya_ditolak_atau_nonstok_dilewati():
    assert nilai(qty=Decimal("0"))[1] == [] and nilai(ber_stok=False)[1] == []


def test_tulis_mengoper_batch_id_ke_kartu_stok():
    src = inspect.getsource(G.tulis_grn)
    assert "_batch_terima(" in src and "batch_id=batch_id" in src
    assert "INSERT INTO item_batches" in inspect.getsource(G._batch_terima)


def test_sidik_lama_tetap_sama():
    body = ReceiveGoodsRequest(receive_date="2026-11-28", items=[{"po_item_id": "x", "quantity_received": 1}])
    assert set(PO._sidik_terima(body)["items"][0]) == {"po_item_id", "quantity_received"}
