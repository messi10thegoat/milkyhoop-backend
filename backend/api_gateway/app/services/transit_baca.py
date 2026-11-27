"""Celah 8: medan aditif `dalam_perjalanan` (saldo gudang sistem Transit) di laporan nilai/mutasi persediaan dan kartu stok barang.
BACA saja, sumber sama dengan laporan masing-masing (ledger gudang is_system; nilai = rumus laporan itu, bukan aturan baru).
None bila tak ada saldo Transit (qty dan nilai nol)."""
from decimal import Decimal
from typing import Optional

from . import teks_galat as tg
from . import teks_laporan_persediaan as TLP

JOIN_TRANSIT = "JOIN warehouses w ON w.id = il.warehouse_id AND w.tenant_id = il.tenant_id AND w.is_system"
_Q = "(COALESCE(il.quantity_in, 0) - COALESCE(il.quantity_out, 0))"

# laporan mutasi: nilai = Σ total_cost bertanda (basis laporan itu)
SQL_MUTASI = (f"SELECT COALESCE(SUM({_Q}), 0) AS qty, COALESCE(SUM(CASE WHEN COALESCE(il.quantity_out, 0) > 0 THEN -il.total_cost ELSE il.total_cost END), 0) AS nilai "
              f"FROM inventory_ledger il {JOIN_TRANSIT} WHERE il.tenant_id = $1 AND il.movement_date <= $2")
# laporan nilai: qty per barang (nilai = qty × HPP-akhir laporan itu)
SQL_NILAI = (f"SELECT il.product_id, COALESCE(SUM({_Q}), 0) AS qty FROM inventory_ledger il {JOIN_TRANSIT} "
             "WHERE il.tenant_id = $1 AND il.movement_date <= $2 GROUP BY il.product_id")
# kartu stok satu barang: qty Transit sampai tanggal + HPP-akhir barang itu sampai tanggal
SQL_KARTU_QTY = (f"SELECT COALESCE(SUM({_Q}), 0) FROM inventory_ledger il {JOIN_TRANSIT} "
                 "WHERE il.tenant_id = $1 AND il.product_id = $2 AND il.movement_date <= $3")
SQL_KARTU_HPP = ("SELECT il.average_cost FROM inventory_ledger il WHERE il.tenant_id = $1 AND il.product_id = $2 AND il.movement_date <= $3 "
                 "ORDER BY il.created_at DESC, il.id DESC LIMIT 1")


def bentuk(qty, nilai, presisi: int) -> Optional[dict]:
    q, n = Decimal(str(qty or 0)), Decimal(str(nilai or 0))
    if q == 0 and n == 0:
        return None
    qt, nt = TLP.qty(q), tg.angka_laporan(n, presisi)
    return {"qty": float(q), "qty_text": qt, "nilai": float(n), "nilai_text": nt, "teks": f"Dalam perjalanan: {qt} · {nt}"}
