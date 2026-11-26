"""G-hapus Gudang (KONTRAK-G-hapus-gudang-20261126; pola U3-3 Hapus Barang): SATU penentu untuk pratinjau DAN penulis hapus PERMANEN.
Accurate: gudang yang sudah dipakai tidak bisa dihapus -> Nonaktifkan; gudang kosong & tak pernah dipakai -> boleh dihapus.
rencana_hapus MURNI (uji unit); ukur_* hanya SELECT. DELETE /warehouses/{id} lama TETAP = nonaktifkan (tak disentuh di sini)."""
from typing import Optional

from .galat_berkode import GalatBerkode

# SATU daftar jejak = SEMUA FK ke warehouses (diukur pg_constraint prod 26 Nov 2026) KECUALI penanda gudang utama (branches.default_warehouse_id,
# pengiriman_preferensi.default_lokasi_kirim_id -> blok GUDANG_UTAMA). (tabel, kolom, syarat tambahan). warehouse_stock qty = 0 = cache kosong, bukan jejak
# (ikut terhapus CASCADE). Tes struktural test_gudang_hapus membandingkan daftar ini dgn FK live: FK baru tanpa entri -> MERAH.
JEJAK_GUDANG = [
    ("inventory_ledger", "warehouse_id", ""),
    ("warehouse_stock", "warehouse_id", " AND quantity <> 0"),
    ("batch_warehouse_stock", "warehouse_id", ""),
    ("stock_adjustments", "warehouse_id", ""),
    ("stock_adjustment_items", "warehouse_id", ""),
    ("stock_transfers", "from_warehouse_id", ""),
    ("stock_transfers", "to_warehouse_id", ""),
    ("stock_opnames", "warehouse_id", ""),
    ("goods_receipts", "warehouse_id", ""),
    ("purchase_orders", "warehouse_id", ""),
    ("bills", "warehouse_id", ""),
    ("sales_orders", "warehouse_id", ""),
    ("sales_orders", "fase_gudang_id", ""),
    ("sales_invoices", "warehouse_id", ""),
    ("sales_receipts", "warehouse_id", ""),
    ("invoice_fulfillments", "warehouse_id", ""),
    ("production_orders", "warehouse_id", ""),
    ("production_order_materials", "warehouse_id", ""),
    ("production_completions", "warehouse_id", ""),
    ("item_serials", "warehouse_id", ""),
    ("serial_movements", "from_warehouse_id", ""),
    ("serial_movements", "to_warehouse_id", ""),
    ("fixed_assets", "warehouse_id", ""),
    ("work_centers", "warehouse_id", ""),
    ("warehouse_bins", "warehouse_id", ""),
    ("stok_minimum_gudang", "warehouse_id", ""),
]
PENANDA_UTAMA = [("branches", "default_warehouse_id"), ("pengiriman_preferensi", "default_lokasi_kirim_id")]
LABEL_JEJAK = {
    "inventory_ledger": "Kartu stok", "warehouse_stock": "Stok gudang", "batch_warehouse_stock": "Stok batch", "stock_adjustments": "Penyesuaian stok",
    "stock_adjustment_items": "Penyesuaian stok", "stock_transfers": "Pemindahan barang", "stock_opnames": "Stok opname", "goods_receipts": "Penerimaan barang",
    "purchase_orders": "Pesanan pembelian", "bills": "Faktur pembelian", "sales_orders": "Pesanan penjualan", "sales_invoices": "Faktur penjualan",
    "sales_receipts": "Penjualan tunai", "invoice_fulfillments": "Pengiriman", "production_orders": "Produksi", "production_order_materials": "Produksi",
    "production_completions": "Produksi", "item_serials": "Nomor seri", "serial_movements": "Nomor seri", "fixed_assets": "Aset tetap",
    "work_centers": "Pusat kerja", "warehouse_bins": "Lokasi rak", "stok_minimum_gudang": "Stok minimum",
}
SQL_HITUNG_JEJAK = "SELECT COUNT(*) FROM (" + " UNION ALL ".join(
    f"SELECT 1 FROM {t} WHERE {c} = $1{s}" for t, c, s in JEJAK_GUDANG) + ") j"   # aksi_tersedia detail; sama dgn JEJAK_GUDANG
TEKS_GALAT_FK = "Gudang masih dipakai data lain, jadi tidak bisa dihapus. Gunakan Nonaktifkan."


def label_jejak(tabel: str) -> str:
    return LABEL_JEJAK.get(tabel, "Transaksi lain")


def _blok(code: str, message: str, **kw) -> dict:
    return {"code": code, "message": message, **kw}


def _hasil(blocks: list, rencana) -> dict:
    return {"ok": not blocks, "blocks": blocks, "peringatan": [], "rencana": rencana}


def _tak_bisa(w: dict, code: str, teks: str, jejak=None, saran=None) -> dict:
    return _hasil([_blok(code, teks, id=str(w["id"]))], {"jenis": "tak_bisa", "jejak": jejak or [], "saran": saran, "teks": teks})


def rencana_hapus(w: Optional[dict], utama: bool, jejak: list) -> dict:
    """Urutan periksa kontrak: TAK_ADA -> SISTEM -> UTAMA -> BERJEJAK. jejak = [(tabel, jumlah)]; utama = gudang bawaan tenant/cabang/lokasi kirim."""
    if not w:
        return _hasil([_blok("GUDANG_TAK_ADA", "Gudang tidak ditemukan.")], None)
    nama = w.get("name") or "Gudang"
    if w.get("is_system"):
        return _tak_bisa(w, "GUDANG_SISTEM", f"{nama} adalah gudang sistem dan tidak bisa dihapus.")
    if utama or w.get("is_default"):
        return _tak_bisa(w, "GUDANG_UTAMA", f"{nama} adalah gudang utama, jadi tidak bisa dihapus. Pilih gudang utama lain dulu.")
    gabung: dict = {}
    for t, n in jejak:   # sumber berlabel sama (mis. dua kolom stock_transfers) dijumlah jadi satu baris
        if n:
            k = label_jejak(t)
            gabung.setdefault(k, {"sumber": t, "sumber_text": k, "jumlah": 0})["jumlah"] += int(n)
    rinci = list(gabung.values())
    if rinci:
        label = [r["sumber_text"] for r in rinci]
        sebut = ", ".join(label[:3]) + (f" dan {len(label) - 3} lainnya" if len(label) > 3 else "")
        return _tak_bisa(w, "GUDANG_BERJEJAK", f"{nama} sudah dipakai di {sebut}, jadi tidak bisa dihapus. Gunakan Nonaktifkan.", rinci, "Nonaktifkan")
    return _hasil([], {"jenis": "hapus_permanen", "jejak": [], "saran": None,
                       "teks": f"{nama} belum pernah dipakai. Menghapus bersifat permanen dan tidak bisa dibatalkan."})


def status_blok(code: str) -> int:
    return 404 if code == "GUDANG_TAK_ADA" else 409


def tolak(r: dict) -> None:
    if r["blocks"]:
        b = r["blocks"][0]
        raise GalatBerkode(status_blok(b["code"]), b["code"], b["message"])


# --- ukur (SELECT saja; id gudang sudah terbukti milik tenant lewat ukur_gudang) ------------------------------------------
async def ukur_gudang(conn, tid: str, warehouse_id) -> Optional[dict]:
    r = await conn.fetchrow("SELECT * FROM warehouses WHERE id = $1 AND tenant_id = $2", warehouse_id, tid)
    return dict(r) if r else None


async def ukur_utama(conn, tid: str, warehouse_id) -> bool:
    for t, c in PENANDA_UTAMA:
        if await conn.fetchval(f"SELECT 1 FROM {t} WHERE {c} = $1 AND tenant_id = $2 LIMIT 1", warehouse_id, tid):
            return True
    return False


async def ukur_jejak(conn, warehouse_id) -> list:
    return [(t, await conn.fetchval(f"SELECT COUNT(*) FROM {t} WHERE {c} = $1{s}", warehouse_id) or 0) for t, c, s in JEJAK_GUDANG]


async def ukur_rencana(conn, tid: str, warehouse_id) -> dict:
    w = await ukur_gudang(conn, tid, warehouse_id)
    if not w:
        return rencana_hapus(None, False, [])
    return rencana_hapus(w, await ukur_utama(conn, tid, warehouse_id), await ukur_jejak(conn, warehouse_id))


async def alasan_tak_bisa(conn, tid: str, w) -> Optional[str]:
    """aksi_tersedia.hapus detail (satu kueri hitung): None = boleh."""
    if w.get("is_system") or w.get("is_default") or await ukur_utama(conn, tid, w["id"]):
        return "Gudang utama atau gudang sistem tidak bisa dihapus."
    if int(await conn.fetchval(SQL_HITUNG_JEJAK, w["id"]) or 0):
        return "Gudang sudah dipakai; gunakan Nonaktifkan."
    return None
