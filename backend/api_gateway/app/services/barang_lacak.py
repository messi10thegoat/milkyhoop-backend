"""Form Barang: lacak nomor seri + metode biaya (27 Nov 2026, kontrak docs/kontrak/KONTRAK-barang-lacak-20261127.md).
SATU penentu untuk buat & ubah. Pola Accurate: pilihan lacak/metode boleh diubah SELAMA barang belum punya riwayat kartu stok.
- track_serial: nyala hanya bila flag serial_v1 tenant NYALA dan barang ber-stok; berubah sesudah ada riwayat stok -> 409.
- costing_method 'fifo': hanya bila flag fifo_v1 NYALA; barang BARU / tanpa riwayat stok -> costing_mulai = tanggal dokumen hari ini
  (stok awal form ikut jadi lapisan pertama). Barang yang sudah bertransaksi -> 409 (butuh lapisan pembuka = unit 4 FIFO, belum ada).
  Kembali ke rata-rata: sebelum ada riwayat bebas; sesudahnya ditolak (T1, juga trigger V510)."""
from fastapi import HTTPException

from .tenant_features import fitur_aktif


def _galat(status: int, code: str, pesan: str) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": pesan})


async def _punya_riwayat(conn, tid: str, product_id) -> bool:
    if product_id is None:
        return False
    return bool(await conn.fetchval("SELECT EXISTS (SELECT 1 FROM inventory_ledger WHERE tenant_id = $1 AND product_id = $2)", tid, product_id))


async def rencana(conn, tid: str, *, product_id, track_serial, costing_method, lama: dict, ber_stok: bool) -> dict:
    """-> {kolom: nilai} untuk UPDATE products (kosong = tak ada yang berubah). lama = baris sekarang ({} untuk barang baru)."""
    ubah: dict = {}
    fitur = None
    serial_lama = bool(lama.get("track_serial"))
    metode_lama = lama.get("costing_method") or "weighted_average"
    if track_serial is not None and bool(track_serial) != serial_lama:
        fitur = fitur if fitur is not None else await fitur_aktif(conn, tid)
        if track_serial and "serial_v1" not in fitur:
            raise _galat(422, "SERIAL_BELUM_AKTIF", "Lacak nomor seri belum aktif untuk usaha ini.")
        if track_serial and not ber_stok:
            raise _galat(422, "SERIAL_BARANG_NON_STOK", "Nomor seri hanya untuk barang yang melacak stok.")
        if await _punya_riwayat(conn, tid, product_id):
            raise _galat(409, "LACAK_SERIAL_TERKUNCI", "Lacak nomor seri tidak bisa diubah: barang ini sudah punya riwayat stok.")
        ubah["track_serial"] = bool(track_serial)
    if costing_method is not None and costing_method != metode_lama:
        if await _punya_riwayat(conn, tid, product_id):
            raise _galat(409, "METODE_BIAYA_TERKUNCI", "Metode biaya tidak bisa diubah: barang ini sudah punya riwayat stok.")
        if costing_method == "fifo":
            fitur = fitur if fitur is not None else await fitur_aktif(conn, tid)
            if "fifo_v1" not in fitur:
                raise _galat(422, "FIFO_BELUM_AKTIF", "Metode FIFO belum aktif; gunakan rata-rata.")
            if not ber_stok:
                raise _galat(422, "FIFO_BARANG_NON_STOK", "Metode FIFO hanya untuk barang yang melacak stok.")
            from ..utils.tanggal_tenant import tanggal_dokumen
            ubah["costing_mulai"] = await tanggal_dokumen(conn, tid)
        else:
            ubah["costing_mulai"] = None
        ubah["costing_method"] = costing_method
    return ubah


async def terapkan(conn, tid: str, product_id, ubah: dict) -> None:
    if not ubah:
        return
    kol = list(ubah)
    await conn.execute(
        f"UPDATE products SET {', '.join(f'{k} = ${i + 3}' for i, k in enumerate(kol))}, updated_at = NOW() WHERE id = $1 AND tenant_id = $2",
        product_id, tid, *[ubah[k] for k in kol])
