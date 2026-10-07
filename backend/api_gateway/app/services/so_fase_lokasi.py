"""Fase LOKASI pesanan penjualan (7 Okt 2026): "Dikirim ke {gudang}" / "Tersedia di {gudang}".

DATA OPERASIONAL SAJA -- NOL jurnal, NOL stok (inventory_ledger/warehouse_stock), NOL HPP. Barang non_inventory tak punya stok; gudang di sini
adalah LOKASI pesanan, bukan lokasi stok. Tak ada angka uang yang ditulis atau diubah (Law 1/16/29: "sisa" tetap turunan jurnal lewat
ringkasan_pesanan; Law 31 gate 4 otomatis lulus -- tak menyentuh CoA mana pun). Tes `test_so_fase_lokasi.py` membuktikan nol SQL ke
journal_*/inventory_*/bank_* dan satu-satunya tabel yang ditulis = sales_orders (+ audit_logs/idempotensi).

Satu penentu untuk pratinjau DAN tulis (`rencana`): blok pertama = galat tulis. Satu transaksi: kunci (Law 13) -> baca baris SO FOR UPDATE ->
penentu -> UPDATE -> riwayat/audit (Law 12). Tenant eksplisit di SETIAP SQL (Law 24; FK komposit (gudang, tenant) menegakkannya juga di DB).
Fase boleh MAJU, MUNDUR, atau DIHAPUS (pola NetSuite custom status: nilai bisa diubah, tiap perubahan tercatat di riwayat dgn lama -> baru).
"""
from typing import Optional

from fastapi import HTTPException

from .so_riwayat import catat_riwayat

FASE = ("dikirim_ke_lokasi", "tersedia")
STATUS_TERTUTUP = ("draft", "cancelled", "completed")   # fase hanya untuk pesanan yang SEDANG BERJALAN


def teks_fase(fase: Optional[str], nama_gudang: Optional[str]) -> Optional[str]:
    """Teks posisi untuk daftar. Gudang tanpa nama -> '—' (tak mengarang)."""
    g = (nama_gudang or "").strip() or "—"
    if fase == "dikirim_ke_lokasi":
        return f"Dikirim ke {g}"
    if fase == "tersedia":
        return f"Tersedia di {g}"
    return None


def _blok(kode: str, pesan: str, status: int = 409) -> dict:
    return {"code": kode, "status": status, "message": pesan}


async def _gudang(conn, tenant_id: str, gudang_id):
    return await conn.fetchrow(
        "SELECT id, name, is_active FROM warehouses WHERE id = $1 AND tenant_id = $2", gudang_id, tenant_id)


async def rencana(conn, ctx: dict, so_id, fase: Optional[str], gudang_id, kunci: bool = False) -> dict:
    """Penentu BACA (kunci=True: baris SO dikunci FOR UPDATE, dipakai jalur tulis). -> {order, gudang, blocks, berubah, lama, baru}.
    SO tak ada / bukan milik tenant -> 404 (sama dengan tak ada)."""
    order = await conn.fetchrow(
        "SELECT id, order_number, status, fase_lokasi, fase_gudang_id FROM sales_orders WHERE id = $1 AND tenant_id = $2"
        + (" FOR UPDATE" if kunci else ""), so_id, ctx["tenant_id"])
    if not order:
        raise HTTPException(status_code=404, detail="Pesanan penjualan tidak ditemukan.")
    blocks, gudang = [], None
    if fase is not None and fase not in FASE:
        blocks.append(_blok("FASE_TIDAK_DIKENAL", "Fase harus 'dikirim_ke_lokasi' atau 'tersedia'.", 422))
    if (fase is None) != (gudang_id is None):
        blocks.append(_blok("FASE_GUDANG_PASANGAN", "Fase dan gudang harus diisi bersamaan (atau dikosongkan bersamaan untuk menghapus).", 422))
    if order["status"] in STATUS_TERTUTUP:
        label = {"draft": "masih draf", "cancelled": "sudah dibatalkan", "completed": "sudah selesai"}[order["status"]]
        blocks.append(_blok("SO_STATUS_TIDAK_BOLEH",
                            f"Pesanan {order['order_number']} {label}; fase lokasi hanya untuk pesanan yang sedang berjalan."))
    if gudang_id is not None:
        gudang = await _gudang(conn, ctx["tenant_id"], gudang_id)
        if not gudang:
            blocks.append(_blok("GUDANG_TAK_ADA", "Gudang tidak ditemukan di usaha ini.", 404))
        elif not gudang["is_active"]:
            blocks.append(_blok("GUDANG_NONAKTIF", f"Gudang {gudang['name']} tidak aktif."))
    lama = {"fase_lokasi": order["fase_lokasi"], "fase_gudang_id": str(order["fase_gudang_id"]) if order["fase_gudang_id"] else None}
    baru = {"fase_lokasi": fase, "fase_gudang_id": str(gudang_id) if gudang_id else None}
    return {"order": order, "gudang": gudang, "blocks": blocks, "berubah": lama != baru, "lama": lama, "baru": baru}


def angkat_blok_pertama(r: dict) -> None:
    if r["blocks"]:
        b = r["blocks"][0]
        raise HTTPException(status_code=b["status"], detail={"code": b["code"], "message": b["message"]})


async def terapkan(conn, ctx: dict, so_id, fase: Optional[str], gudang_id) -> dict:
    """Inti tulis, di transaksi PEMANGGIL. -> data respons (berubah=False bila sama persis: tanpa tulis, tanpa riwayat)."""
    await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", f"SO_FASE:{ctx['tenant_id']}:{so_id}")
    r = await rencana(conn, ctx, so_id, fase, gudang_id, kunci=True)
    angkat_blok_pertama(r)
    order, gudang = r["order"], r["gudang"]
    nama_baru = gudang["name"] if gudang else None
    if r["berubah"]:
        if fase is None:
            await conn.execute(
                "UPDATE sales_orders SET fase_lokasi = NULL, fase_gudang_id = NULL, fase_at = NULL, updated_at = NOW() "
                "WHERE id = $1 AND tenant_id = $2", so_id, ctx["tenant_id"])
        else:
            await conn.execute(
                "UPDATE sales_orders SET fase_lokasi = $3, fase_gudang_id = $4, fase_at = NOW(), updated_at = NOW() "
                "WHERE id = $1 AND tenant_id = $2", so_id, ctx["tenant_id"], fase, gudang_id)
        nama_lama = None
        if order["fase_gudang_id"]:
            g0 = await _gudang(conn, ctx["tenant_id"], order["fase_gudang_id"])
            nama_lama = g0["name"] if g0 else None
        dulu = teks_fase(order["fase_lokasi"], nama_lama)
        kini = teks_fase(fase, nama_baru)
        ringkas = (f"Posisi pesanan: {kini}" + (f" (sebelumnya {dulu})" if dulu else "")) if kini else f"Posisi lokasi dihapus (sebelumnya {dulu})"
        await catat_riwayat(conn, ctx["tenant_id"], "sales_orders", order["id"], order["order_number"], "SO_FASE_LOKASI_CHANGED",
                            ctx["user_id"], ringkas, {"lama": r["lama"], "baru": r["baru"], "gudang": nama_baru},
                            source="api:sales_orders.fase_lokasi")
    return {"id": str(order["id"]), "order_number": order["order_number"], "fase_lokasi": fase,
            "fase_gudang_id": str(gudang_id) if gudang_id else None, "fase_gudang_nama": nama_baru,
            "position_text": teks_fase(fase, nama_baru), "berubah": r["berubah"]}
