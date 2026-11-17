"""BE-A (17 Nov 2026): Batch standar SO + BUG-164 -- SATU penentu keadaan, teks tampil, aksi_tersedia, dan rencana penyesuaian batch.

Penyesuaian batch = DOKUMEN Penyesuaian Stok bernomor (+ jurnal persediaan + kartu stok) -- putusan pemilik 17 Nov (ikut Accurate). Dulu
POST /item-batches/{id}/adjust hanya mengubah batch_warehouse_stock: tanpa kartu stok, tanpa jurnal, stok batch dan stok barang bisa selisih.
Sekarang: rencana (pratinjau == penulis) -> penulis SA yang SAMA dgn penyesuaian cepat -> baris ledger dokumen itu ditandai batch_id ->
saldo batch di gudang itu bergerak. Pembatalan dokumen SA membalik batch lewat record_inventory_reversal (ledger.batch_id), tanpa kode baru.
Hanya MEMBACA di sini; penulisan ada di routers/item_batches.py."""
from datetime import date
from decimal import Decimal
from typing import Optional

from . import stok_sesuai as SS

AKSI = ["sesuaikan", "ubah", "karantina", "aktifkan"]
NAMA_STATUS = {"active": "Aktif", "expired": "Kedaluwarsa", "depleted": "Habis", "quarantine": "Karantina"}


def _d(x) -> Decimal:
    return Decimal(str(x if x is not None else 0))


def angka(x) -> str:
    """Bilangan tampil: tanpa nol belakang (12,5 bukan 12,500000)."""
    d = _d(x)
    teks = format(d.normalize(), "f") if d != d.to_integral() else str(int(d))
    return teks.replace(".", ",")


def keadaan(status: str, expiry: Optional[date], hari_ini: date) -> str:
    """Status tampil: batch 'active' yang lewat tanggal kedaluwarsa dihitung Kedaluwarsa (kolom status bisa basi sampai ada yang memperbaruinya)."""
    if status == "active" and expiry and expiry < hari_ini:
        return "expired"
    return status


def status_text(status: str, expiry: Optional[date], hari_ini: date) -> str:
    return NAMA_STATUS.get(keadaan(status, expiry, hari_ini), str(status))


def expiry_text(expiry: Optional[date], hari_ini: date) -> str:
    if not expiry:
        return "Tanpa tanggal kedaluwarsa"
    n = (expiry - hari_ini).days
    if n < 0:
        return f"Kedaluwarsa {-n} hari lalu"
    if n == 0:
        return "Kedaluwarsa hari ini"
    return f"Kedaluwarsa {n} hari lagi"


def stock_text(qty, unit: Optional[str]) -> str:
    return f"{angka(qty)} {unit}".strip() if unit else angka(qty)


def position_text(b: dict, hari_ini: date) -> str:
    """Satu kalimat posisi: '<stok> · <status> · <kedaluwarsa>' (status Aktif tanpa tanggal dihilangkan agar ringkas)."""
    bagian = [stock_text(b.get("current_quantity"), b.get("base_unit")), status_text(b["status"], b.get("expiry_date"), hari_ini)]
    if b.get("expiry_date"):
        bagian.append(expiry_text(b["expiry_date"], hari_ini))
    return " · ".join(bagian)


def aksi_tersedia(b: dict, hari_ini: date) -> dict:
    """Satu penentu untuk daftar, detail, dan penolak tulis: {aksi: {tersedia, alasan}}."""
    ada = {"tersedia": True, "alasan": None}
    tak = lambda s: {"tersedia": False, "alasan": s}
    st = keadaan(b["status"], b.get("expiry_date"), hari_ini)
    a = {"ubah": dict(ada), "sesuaikan": dict(ada)}
    a["karantina"] = tak("Batch sudah dikarantina.") if st == "quarantine" else (tak("Batch sudah habis.") if st == "depleted" else dict(ada))
    a["aktifkan"] = dict(ada) if st == "quarantine" else tak("Hanya batch karantina yang bisa diaktifkan kembali.")
    return a


def hiasi(b: dict, hari_ini: date) -> dict:
    """Medan standar tampil + aksi untuk satu baris batch (dict dari item_batches + item)."""
    return {"status_text": status_text(b["status"], b.get("expiry_date"), hari_ini),
            "expiry_text": expiry_text(b.get("expiry_date"), hari_ini),
            "stock_text": stock_text(b.get("current_quantity"), b.get("base_unit")),
            "position_text": position_text(b, hari_ini),
            "aksi_tersedia": aksi_tersedia(b, hari_ini), "aksi_semua": list(AKSI)}


async def muat_batch(conn, tid: str, batch_id, kunci: bool = False):
    return await conn.fetchrow(
        "SELECT ib.*, p.nama_produk AS item_name, p.sku AS item_code, p.base_unit, p.item_type, p.track_inventory "
        "FROM item_batches ib JOIN products p ON p.id = ib.item_id AND p.tenant_id = ib.tenant_id "
        "WHERE ib.id = $1 AND ib.tenant_id = $2" + (" FOR UPDATE OF ib" if kunci else ""), batch_id, tid)


async def stok_batch_gudang(conn, tid: str, batch_id, warehouse_id) -> Decimal:
    return _d(await conn.fetchval(
        "SELECT COALESCE(SUM(quantity), 0) FROM batch_warehouse_stock WHERE tenant_id = $1 AND batch_id = $2 AND warehouse_id = $3",
        tid, batch_id, warehouse_id))


async def rencana_sesuaikan(conn, tid: str, batch_id, body, bl, kunci: bool = False) -> dict:
    """Penentu BERSAMA pratinjau + penulis: batch milik usaha, gudang sah, jumlah != 0, barang berstok, batch tak cukup/stok barang tak cukup.
    -> {blocks, batch, gudang, ubah, batch_sebelum, batch_sesudah, stok_sebelum, stok_sesudah, isyarat}. Pratinjau: blok dikumpulkan; tulis: blok pertama dilempar."""
    hasil = {"blocks": bl.daftar, "batch": None, "gudang": None, "ubah": Decimal(0), "batch_sebelum": None, "batch_sesudah": None,
             "stok_sebelum": None, "stok_sesudah": None, "isyarat": []}
    b = await muat_batch(conn, tid, batch_id, kunci)
    if not b:
        bl.galat("BATCH_TIDAK_DITEMUKAN", 404, "Batch tidak ditemukan.")
        return hasil
    hasil["batch"] = b
    ubah = _d(body.quantity_change)
    hasil["ubah"] = ubah
    if ubah == 0:
        bl.galat("JUMLAH_NOL", 422, "Jumlah penyesuaian tidak boleh 0.")
    if not (b["item_type"] == "goods" and (b["track_inventory"] if b["track_inventory"] is not None else True)):
        bl.galat("BARANG_TANPA_STOK", 422, f"{b['item_name']} tidak melacak stok; penyesuaian batch tidak berlaku.")
    g = await SS.gudang(conn, tid, body.warehouse_id, bl)
    hasil["gudang"] = g
    if not g:
        return hasil
    sebelum = hasil["batch_sebelum"] = await stok_batch_gudang(conn, tid, b["id"], g["id"])
    hasil["batch_sesudah"] = sebelum + ubah
    if sebelum + ubah < 0:
        bl.galat("BATCH_STOK_TIDAK_CUKUP", 422,
                 f"Stok batch {b['batch_number']} di {g['name']} tidak cukup: tersedia {angka(sebelum)}, dikurangi {angka(abs(ubah))}.")
    stok = hasil["stok_sebelum"] = await SS.stok_gudang(conn, tid, b["item_id"], g["id"])
    hasil["stok_sesudah"] = stok + ubah
    if stok + ubah < 0:
        bl.galat("STOK_TIDAK_CUKUP", 422, f"Stok {b['item_name']} di {g['name']} tidak cukup: tersedia {angka(stok)}, dikurangi {angka(abs(ubah))}.")
    return hasil
