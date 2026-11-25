"""BUG-268b (17 Nov 2026; putusan MASTER): snapshot inventory_ledger.average_cost pada baris PEMBALIK (void) = WAC SESUDAH efek baris itu (nilai bersih ÷ qty bersih), bukan WAC
sebelum insert. Dulu void penerimaan meninggalkan WAC yang masih memuat penerimaan itu (kaos: Cm20s-2 128.000 -> 100.235,29; BRG-0001 43.069,31 -> 23.844,83) -> drift guard V181 /
kartu stok / laporan nilai persediaan. Cadangan (qty<=0, nilai<0) = snapshot sebelumnya (pola V480). Tanpa UPDATE ledger (Rule 8)."""
import asyncio
import inspect
import uuid
from datetime import date
from decimal import Decimal as D

import pytest

from app.services import inventory_helpers as IH

T = "t1"
P = uuid.uuid4()
W = uuid.uuid4()
HARI = date(2026, 11, 17)


def baris(masuk=0, keluar=0, uc=0, avg=None, source="X", source_id=None, wh=W):
    return {"id": uuid.uuid4(), "product_id": P, "product_code": "A-1", "product_name": "Aqua", "quantity_in": D(str(masuk)), "quantity_out": D(str(keluar)),
            "unit_cost": D(str(uc)), "total_cost": D(str(masuk or keluar)) * D(str(uc)), "warehouse_id": wh, "source_number": "DOK-1", "batch_id": None,
            "average_cost": None if avg is None else D(str(avg)), "source_type": source, "source_id": source_id}


class Buku:
    """Ledger palsu: fetch (baris sumber), fetchrow (saldo+nilai), fetchval (get_weighted_average_cost ala V480), execute INSERT (menambah baris -> keadaan berevolusi)."""
    def __init__(self, rows):
        self.rows, self.insert = rows, []

    def _qty_nilai(self):
        q = sum((r["quantity_in"] - r["quantity_out"] for r in self.rows), D(0))
        v = sum((r["quantity_in"] * r["unit_cost"] - r["quantity_out"] * r["unit_cost"] for r in self.rows), D(0))
        return q, v

    async def fetch(self, sql, *a):
        assert "FROM inventory_ledger" in sql
        return [r for r in self.rows if r["source_type"] == a[1] and r["source_id"] == a[2]]

    async def fetchrow(self, sql, *a):
        q, v = self._qty_nilai()
        return {"balance": q, "nilai": v}

    async def fetchval(self, sql, *a):
        if "stock_opnames" in sql:
            return None   # Stok Opname: penjaga gudang terkunci (assert_gudang_tidak_terkunci) -- tak ada opname
        if "tenant_features" in sql:
            return False  # FIFO unit 2: barang WAC (fifo_aktif)
        assert "get_weighted_average_cost" in sql
        q, v = self._qty_nilai()
        if q > 0 and v >= 0:
            return (v / q).quantize(D("0.01"))
        ada = [r["average_cost"] for r in self.rows if r["average_cost"] is not None]
        return ada[-1] if ada else D(0)

    async def execute(self, sql, *a):
        if sql.lstrip().startswith("INSERT INTO inventory_ledger"):
            self.insert.append(a)
            self.rows.append({"id": a[0], "product_id": a[2], "product_code": a[3], "product_name": a[4], "quantity_in": a[10], "quantity_out": a[11], "unit_cost": a[13],
                              "total_cost": a[14], "average_cost": a[15], "warehouse_id": a[16], "source_number": a[9], "batch_id": None, "source_type": a[7], "source_id": a[8]})
        return "OK"


def jalan(buku, sumber, sid):
    return asyncio.run(IH.record_inventory_reversal(buku, T, sumber, sid, uuid.uuid4(), reversal_date=HARI))


def test_void_penerimaan_memulihkan_wac_kasus_kaos_cm20s():
    sid = uuid.uuid4()
    buku = Buku([baris(masuk=3.25, uc=128000, avg=128000, source="OPENING"),
                 baris(masuk=1, uc=10000, avg="100235.29", source="BILL", source_id=sid)])    # WAC sesudah terima = (416000+10000)/4,25 = 100235,29
    jalan(buku, "BILL", sid)
    pembalik = buku.insert[0]
    assert pembalik[7] == "BILL_VOID" and pembalik[11] == D("1") and pembalik[10] == D("0")
    assert pembalik[15] == D("128000.00")                       # average_cost pembalik = WAC SESUDAH void (bukan 100235,29)
    assert buku.rows[-1]["average_cost"] == D("128000.00")


def test_void_sesudah_dua_penerimaan_hanya_yang_dibatalkan_keluar_dari_wac():
    s1, s2 = uuid.uuid4(), uuid.uuid4()
    buku = Buku([baris(masuk=10, uc=100, avg=100, source="BILL", source_id=s1), baris(masuk=5, uc=200, avg="133.33", source="BILL", source_id=s2)])
    jalan(buku, "BILL", s2)
    assert buku.insert[0][15] == D("100.00")                    # tinggal penerimaan pertama: 1000/10


def test_void_pengiriman_restock_pada_biaya_asli_wac_tetap_wajar():
    sid = uuid.uuid4()
    buku = Buku([baris(masuk=10, uc=100, avg=100, source="BILL"), baris(keluar=4, uc=100, avg=100, source="INVOICE_FULFILLMENT", source_id=sid)])
    jalan(buku, "INVOICE_FULFILLMENT", sid)
    p = buku.insert[0]
    assert p[7] == "INVOICE_FULFILLMENT_VOID" and p[10] == D("4") and p[15] == D("100.00")     # barang kembali pada biaya penjualan 100 -> WAC 1000/10


def test_void_sesudah_ada_keluar_qty_nol_atau_minus_pakai_cadangan_bukan_negatif():
    s1 = uuid.uuid4()
    buku = Buku([baris(masuk=10, uc=100, avg=100, source="BILL", source_id=s1), baris(keluar=6, uc=100, avg=100, source="INVOICE_FULFILLMENT")])
    jalan(buku, "BILL", s1)                                      # void 10 setelah 6 terjual -> qty -6
    assert buku.insert[0][15] == D("100.00") and buku.insert[0][15] > 0   # cadangan = snapshot sebelumnya, tak pernah negatif/0


def test_dua_baris_satu_produk_dihitung_ulang_tiap_baris():
    sid = uuid.uuid4()
    buku = Buku([baris(masuk=10, uc=100, avg=100, source="OPENING"),
                 baris(masuk=2, uc=400, avg="150.00", source="BILL", source_id=sid), baris(masuk=3, uc=200, avg="153.85", source="BILL", source_id=sid)])
    jalan(buku, "BILL", sid)
    assert [i[15] for i in buku.insert] == [D("123.08"), D("100.00")]    # keadaan 15 / 2400: balik 2@400 -> 13 / 1600 = 123,08; lalu 3@200 -> 10 / 1000 = 100


@pytest.mark.parametrize("nilai,qty,masuk,keluar,uc,cad,harap", [
    ("426000", "4.25", 0, 1, "10000", "100235.29", "128000.00"),   # sesudah terima: 426.000 / 4,25; void 1@10.000 -> 416.000 / 3,25
    ("1000", "10", 0, 0, "50", "7", "100.00"),                   # gerakan nol: nilai/qty apa adanya
    ("100", "3", 0, 0, "1", "0", "33.33"), ("200", "3", 0, 0, "1", "0", "66.67"),   # HALF_UP 2 desimal
    ("201", "200", 0, 0, "1", "0", "1.01"),                      # 1,005 -> 1,01 (HALF_UP, bukan genap)
    ("100", "10", 0, 10, "10", "55", "55"),                      # qty sesudah 0 -> cadangan
    ("100", "10", 0, 12, "10", "55", "55"),                      # qty < 0 -> cadangan
    ("100", "10", 0, 5, "30", "55", "55"),                       # nilai sesudah < 0 (100-150) -> cadangan
])
def test_hitung_wac_sesudah(nilai, qty, masuk, keluar, uc, cad, harap):
    assert IH.hitung_wac_sesudah(D(nilai), D(qty), D(str(masuk)), D(str(keluar)), D(uc), D(cad)) == D(harap)


def test_pembalik_tak_mengubah_baris_lama_dan_hanya_menulis_kolom_snapshot_pada_baris_baru():
    src = inspect.getsource(IH.record_inventory_reversal)
    assert "UPDATE inventory_ledger" not in src and "DELETE FROM inventory_ledger" not in src      # Rule 8: append-only
    assert "snapshot_wac_sesudah(" in src and "saldo_dan_nilai(" in src
    assert src.index("snapshot_wac_sesudah(") < src.index("tulis_kartu(")             # dihitung SEBELUM baris dituliskan, memakai efeknya
    hs = inspect.getsource(IH.snapshot_wac_sesudah)
    assert "hitung_wac_sesudah(" in hs and "SELECT get_weighted_average_cost($1, $2)" in hs            # get_weighted_average_cost hanya cadangan
    assert "SUM(quantity_in * unit_cost) - SUM(quantity_out * unit_cost)" in inspect.getsource(IH.saldo_dan_nilai)
