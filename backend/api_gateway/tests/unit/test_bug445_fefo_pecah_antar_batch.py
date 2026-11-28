"""BUG-445: kirim barang ber-batch tanpa batch pilihan, batch FEFO pertama kurang -> dipecah antar-batch (L1 4 + L2 2) selama total ber-batch cukup."""
import asyncio
import uuid
from decimal import Decimal as D

from app.services import batch_alokasi as BAL
from app.services import satuan_dasar as SD

II, PID, L1, L2 = uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4()


class Kon:
    def __init__(self, track=True, batches=((L1, 4), (L2, 2)), unit="pcs"):
        self.track, self.batches, self.unit = track, batches, unit

    async def fetch(self, sql, *a):
        if "FROM sales_invoice_items" in sql:
            return [{"id": II, "item_id": PID, "unit": self.unit}] if self.track else []
        if "get_available_batches" in sql:
            return [{"batch_id": b, "quantity_to_use": D(q)} for b, q in self.batches]
        raise AssertionError(sql)


def jalan(k, qty, f=D(1), monkeypatch=None, **extra):
    async def qd(conn, tid, pid, unit, q, mode):
        return {"qty_dasar": D(q) * f, "faktor": f}
    asli = SD.qty_dasar
    SD.qty_dasar = qd
    try:
        return asyncio.run(BAL.pecah_fefo(k, "t", uuid.uuid4(), [{"invoice_item_id": II, "quantity": D(qty), **extra}]))
    finally:
        SD.qty_dasar = asli


def test_dipecah_dua_batch():
    r = jalan(Kon(), 6)
    assert [(x["batch_id"], x["quantity"]) for x in r] == [(L1, D(4)), (L2, D(2))]


def test_satu_batch_cukup_tak_dipecah():
    r = jalan(Kon(batches=((L1, 6),)), 6)
    assert len(r) == 1 and not r[0].get("batch_id")


def test_total_batch_kurang_tak_dipecah_inti_yang_memblok():
    r = jalan(Kon(batches=((L1, 4), (L2, 1))), 6)
    assert len(r) == 1 and not r[0].get("batch_id")


def test_batch_pilihan_atau_serial_atau_bukan_barang_batch_tak_disentuh():
    assert jalan(Kon(), 6, batch_id=L2)[0]["batch_id"] == L2
    assert len(jalan(Kon(track=False), 6)) == 1


def test_satuan_dokumen_dus_pecahan_dalam_dus():
    r = jalan(Kon(batches=((L1, 24), (L2, 12))), 3, f=D(12))
    assert [(x["batch_id"], x["quantity"]) for x in r] == [(L1, D(2)), (L2, D(1))]


def test_pecahan_tak_habis_dibagi_faktor_tak_dipecah():
    assert len(jalan(Kon(batches=((L1, 5), (L2, 7))), 1, f=D(12))) == 1


def test_inti_mencatat_batch_hasil_fefo():
    import inspect
    import app.routers.sales_invoices as SI
    src = inspect.getsource(SI._execute_fulfillment)
    assert '_fi_rec["batch_id"] = batch_id' in src and "batch_alokasi.pecah_fefo(" in src
