"""BUG-268c (17 Nov 2026; putusan MASTER; lanjutan 268b): penulis baris PEMBALIK lain -- void Penjualan Tunai/POS (sales_receipts.void_sales_receipt) dan pembatalan Work Order
(production._reverse_inventory_ledger) -- dulu menulis average_cost = unit_cost baris asli (bukan WAC). Kini SATU penentu inventory_helpers.snapshot_wac_sesudah (WAC SESUDAH efek)."""
import asyncio
import inspect
import pathlib
import uuid
from datetime import date
from decimal import Decimal as D

from app.services import inventory_helpers as IH

APP = pathlib.Path(__file__).resolve().parents[2] / "app"
T, P, W = "t1", uuid.uuid4(), uuid.uuid4()


def baris(masuk=0, keluar=0, uc=0, avg=None, source="X", source_id=None):
    return {"id": uuid.uuid4(), "product_id": P, "product_code": "A-1", "product_name": "Aqua", "quantity_in": D(str(masuk)), "quantity_out": D(str(keluar)), "unit_cost": D(str(uc)),
            "total_cost": D(str(masuk or keluar)) * D(str(uc)), "warehouse_id": W, "source_number": "DOK-1", "average_cost": avg, "source_type": source, "source_id": source_id}


class Buku:
    def __init__(self, rows):
        self.rows, self.insert = rows, []

    def _qv(self):
        return (sum((r["quantity_in"] - r["quantity_out"] for r in self.rows), D(0)),
                sum((r["quantity_in"] * r["unit_cost"] - r["quantity_out"] * r["unit_cost"] for r in self.rows), D(0)))

    async def fetch(self, sql, *a):
        return [r for r in self.rows if r["source_type"] == a[1] and r["source_id"] == a[2]]

    async def fetchrow(self, sql, *a):
        q, v = self._qv()
        return {"balance": q, "nilai": v}

    async def fetchval(self, sql, *a):
        q, v = self._qv()
        if "get_weighted_average_cost" in sql:
            return (v / q).quantize(D("0.01")) if q > 0 and v >= 0 else None
        return q          # SELECT COALESCE(SUM(quantity_in - quantity_out), 0) ...

    async def execute(self, sql, *a):
        if "INSERT INTO inventory_ledger" in sql:
            self.insert.append(a)
            self.rows.append({**baris(), "quantity_in": a[8], "quantity_out": a[9], "unit_cost": a[11], "total_cost": a[12], "average_cost": a[18], "source_type": a[5], "source_id": a[6]})
        return "OK"


def test_snapshot_wac_sesudah_restock_pos_dan_batal_wo():
    # restock POS: ada 3 pcs bernilai 300 (WAC 100); barang 2 pcs kembali pada biaya 160 (biaya jual lama) -> (300+320)/5 = 124,00
    buku = Buku([baris(masuk=3, uc=100, avg=100)])
    assert asyncio.run(IH.snapshot_wac_sesudah(buku, T, P, 2, 0, 160)) == D("124.00")
    # batal WO: hasil jadi 10 @ 50 masuk ke stok 10 @ 100 (WAC 75); dibatalkan -> keluar 10 @ 50 -> kembali 100,00
    buku2 = Buku([baris(masuk=10, uc=100, avg=100), baris(masuk=10, uc=50, avg=75, source="PRODUCTION_OUTPUT")])
    assert asyncio.run(IH.snapshot_wac_sesudah(buku2, T, P, 0, 10, 50)) == D("100.00")
    # stok habis sesudah efek -> cadangan (get_weighted_average_cost sebelumnya, lalu unit_cost), tak pernah 0
    buku3 = Buku([baris(masuk=4, uc=100, avg=100)])
    assert asyncio.run(IH.snapshot_wac_sesudah(buku3, T, P, 0, 4, 100)) == D("100.00")
    buku4 = Buku([])
    assert asyncio.run(IH.snapshot_wac_sesudah(buku4, T, P, 0, 1, 77)) == D("77")
    assert asyncio.run(IH.snapshot_wac_sesudah(Buku([]), T, P, 1, 0, 40, saldo=(D("2"), D("20")))) == D("20.00")   # saldo dari pemanggil DIPAKAI (ledger palsu kosong): (20+40)/3


def test_pembalik_wo_ditulis_dengan_wac_sesudah_bukan_unit_cost_asli():
    from app.routers import production as PR
    sid = uuid.uuid4()
    buku = Buku([baris(masuk=10, uc=100, avg=100, source="OPENING"), baris(masuk=10, uc=50, avg=75, source="PRODUCTION_OUTPUT", source_id=sid)])
    asyncio.run(PR._reverse_inventory_ledger(buku, T, "u1", "PRODUCTION_OUTPUT", sid, uuid.uuid4(), "PRODUCTION_OUTPUT", hari_ini=date(2026, 11, 17)))
    a = buku.insert[0]
    assert (a[8], a[9], a[11]) == (D("0"), D("10"), D("50")) and a[18] == D("100.00")      # unit_cost 50 TETAP; average_cost = WAC sesudah (bukan 50)


def test_sumber_void_pos_dan_wo_memakai_penentu_bersama_dan_parameter_snapshot():
    pos = inspect.getsource(__import__("app.routers.sales_receipts", fromlist=["x"]).void_sales_receipt)
    assert "_IH.snapshot_wac_sesudah(conn, ctx[\"tenant_id\"], item[\"item_id\"], _qdv[\"qty_dasar\"], 0, item[\"unit_cost\"])" in pos and "$9, $10, $15," in pos and "_avg,\n" in pos
    assert "$9, $10, $9," not in pos                                       # pola lama (average_cost = unit_cost) hilang
    wo = inspect.getsource(__import__("app.routers.production", fromlist=["x"])._reverse_inventory_ledger)
    assert "_IH.snapshot_wac_sesudah(conn, tenant_id, r[\"product_id\"], new_in, new_out, r[\"unit_cost\"])" in wo and "$12, $13, $19," in wo and "avg_sesudah,\n" in wo
    assert "$12, $13, $12," not in wo
    for src in (pos, wo):
        assert "UPDATE inventory_ledger" not in src and "DELETE FROM inventory_ledger" not in src      # Rule 8


def test_pembalik_umum_juga_lewat_penentu_bersama():
    src = inspect.getsource(IH.record_inventory_reversal)
    assert "snapshot_wac_sesudah(conn, tenant_id, product_id, rev_qty_in, rev_qty_out, unit_cost, (current_balance, nilai_sebelum))" in src and "saldo_dan_nilai(conn, tenant_id, product_id)" in src
