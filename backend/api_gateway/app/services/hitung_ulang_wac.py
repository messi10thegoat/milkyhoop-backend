"""BUG-393 (putusan pemilik LANGSUNG 26 Nov: void wajib memulihkan HPP rata-rata, pola Accurate = hitung ulang dari kartu).
Kolom inventory_ledger.average_cost = snapshot WAC SESUDAH tiap gerakan (dibaca laporan nilai persediaan, kartu stok, guard V181). Baris yang ditulis
SEBELUM BUG-268b (17 Nov) oleh void/pembalik menyimpan WAC lama -> laporan Σ qty x HPP-akhir menyimpang dari alir ledger (= GL).
Hitung ulang = jalani kartu barang urut aplikasi (created_at, id) dgn rumus SAMA hitung_wac_sesudah. Hanya kolom turunan average_cost;
qty, unit_cost, total_cost, jurnal TIDAK disentuh. Barang FIFO dilewati (lapisan = penentu, bukan WAC)."""
from decimal import Decimal

from .inventory_helpers import hitung_wac_sesudah

SQL_KARTU = ("SELECT id, quantity_in, quantity_out, unit_cost, average_cost FROM inventory_ledger "
             "WHERE tenant_id = $1 AND product_id = $2 ORDER BY created_at, id")
SQL_UBAH = "UPDATE inventory_ledger SET average_cost = $3 WHERE tenant_id = $1 AND id = $2"


def _d(x) -> Decimal:
    return Decimal(str(x or 0))


def rencana(kartu: list) -> list:
    """MURNI. kartu urut aplikasi -> [(id, lama, baru)] untuk baris yang snapshot-nya berbeda dari hasil hitung ulang."""
    qty, nilai, wac, beda = Decimal(0), Decimal(0), None, []
    for r in kartu:
        masuk, keluar, uc = _d(r["quantity_in"]), _d(r["quantity_out"]), _d(r["unit_cost"])
        baru = hitung_wac_sesudah(nilai, qty, masuk, keluar, uc, wac if wac is not None else uc)
        if baru != _d(r["average_cost"]):
            beda.append((r["id"], _d(r["average_cost"]), baru))
        qty, nilai, wac = qty + masuk - keluar, nilai + (masuk - keluar) * uc, baru
    return beda


async def hitung_ulang(conn, tid: str, product_id, terapkan: bool = False) -> list:
    """Kunci STOK:{tid}:{barang} (Law 13 v3.8, sama dgn tagihan_aksi); terapkan=False = uji kering. Panggil di dalam transaksi."""
    from . import fifo_lapisan
    if await fifo_lapisan.fifo_aktif(conn, tid, product_id):
        return []
    await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", f"STOK:{tid}:{product_id}")
    beda = rencana(await conn.fetch(SQL_KARTU, tid, product_id))
    if terapkan:
        for lid, _, baru in beda:
            await conn.execute(SQL_UBAH, tid, lid, baru)
    return beda
