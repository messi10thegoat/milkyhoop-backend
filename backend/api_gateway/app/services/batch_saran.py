"""Saran pengambilan batch lintas gudang (BT-1b). BACA saja: tak menulis stok, uang, atau jurnal.

Urutan FEFO (kedaluwarsa terdekat dulu, tanpa tanggal terakhir) atau FIFO (tertua dibuat dulu).
Batch kedaluwarsa/karantina tetap TAMPIL (agar pengguna tahu) tapi tak pernah disarankan.
"""
from datetime import date
from decimal import Decimal
from typing import Optional

from . import batch_aksi as BA

METODE = ("FEFO", "FIFO")

SQL_BATCH = """
SELECT ib.id AS batch_id, ib.batch_number, ib.expiry_date, ib.status, ib.created_at,
       bws.warehouse_id, w.name AS warehouse_name, bws.available_quantity
FROM item_batches ib
JOIN batch_warehouse_stock bws ON bws.batch_id = ib.id
JOIN warehouses w ON w.id = bws.warehouse_id
WHERE ib.tenant_id = $1 AND ib.item_id = $2
  AND ($3::uuid IS NULL OR bws.warehouse_id = $3)
  AND bws.available_quantity > 0
  AND ib.status IN ('active', 'expired', 'quarantine')
"""


def _kunci(metode: str):
    if metode == "FIFO":
        return lambda r: (r["created_at"], str(r["batch_id"]), str(r["warehouse_id"]))
    return lambda r: (r["expiry_date"] is None, r["expiry_date"] or date.max, r["created_at"], str(r["batch_id"]), str(r["warehouse_id"]))


def susun(rows: list, qty, metode: str, hari_ini: date, unit: Optional[str]) -> dict:
    """rows = baris SQL_BATCH. Mengembalikan {urutan, cukup, kurang_text, ringkas_text}."""
    butuh = Decimal(str(qty))
    sisa = butuh
    urutan = []
    for r in sorted(rows, key=_kunci(metode)):
        ada = Decimal(str(r["available_quantity"]))
        st = BA.keadaan(r["status"], r["expiry_date"], hari_ini)
        layak = st == "active"
        ambil = min(sisa, ada) if layak else Decimal(0)
        sisa -= ambil
        urutan.append({
            "batch_id": r["batch_id"], "batch_no": r["batch_number"], "exp_date": r["expiry_date"],
            "exp_text": BA.expiry_text(r["expiry_date"], hari_ini),
            "warehouse_id": r["warehouse_id"], "warehouse_name": r["warehouse_name"],
            "available_quantity": ada, "available_text": BA.stock_text(ada, unit),
            "saran_qty": ambil, "saran_text": BA.stock_text(ambil, unit),
            "kedaluwarsa": st == "expired", "status_text": BA.NAMA_STATUS.get(st, str(st))})
    cukup = sisa <= 0
    kurang_text = None if cukup else f"Kurang {BA.stock_text(sisa, unit)}"
    dipakai = [u for u in urutan if u["saran_qty"] > 0]
    if cukup:
        ringkas = f"{BA.stock_text(butuh, unit)} dari {len(dipakai)} batch ({metode})"
    elif dipakai:
        ringkas = f"Batch layak hanya mencukupi {BA.stock_text(butuh - sisa, unit)} dari {BA.stock_text(butuh, unit)}. {kurang_text}"
    else:
        ringkas = f"Tidak ada batch layak untuk {BA.stock_text(butuh, unit)}. {kurang_text}"
    return {"urutan": urutan, "cukup": cukup, "kurang_text": kurang_text, "ringkas_text": ringkas}


def tanpa_batch(qty, unit: Optional[str]) -> dict:
    return {"urutan": [], "cukup": True, "kurang_text": None,
            "ringkas_text": "Barang ini tidak memakai batch; stok diambil tanpa pilihan batch."}
