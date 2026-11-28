"""Alokasi BATCH per baris Faktur Penjualan (paritas butir 9, ikut Accurate; V515 GO pemilik LANGSUNG 27 Nov).
Satu baris faktur boleh diambil dari beberapa batch: allocations = [{batch_id, quantity}] (SATUAN DASAR; baris wajib bersatuan dasar).
HPP TIDAK berubah (rata-rata/FIFO per BARANG; batch = keterangan, T3). Saat kirim, baris dipecah per alokasi (satu gerak kartu stok per batch).
Kode hidup SEBELUM V515: tanpa allocations = nol kueri; baca memakai to_regclass."""
from decimal import Decimal
from typing import List, Optional
from uuid import UUID

from fastapi import HTTPException
from pydantic import BaseModel, Field

TABEL = "sales_invoice_item_batches"


class Alokasi(BaseModel):
    batch_id: UUID
    quantity: Decimal = Field(..., gt=0)


def _g(status: int, code: str, pesan: str) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": pesan})


async def _ada_tabel(conn) -> bool:
    return bool(await conn.fetchval("SELECT to_regclass('public.sales_invoice_item_batches') IS NOT NULL"))


async def tulis(conn, tid: str, invoice_id, line_number: int, product_id, quantity, unit, allocations: Optional[list]) -> None:
    """Tulis alokasi baris (draf/buat/ubah). None = tak ada alokasi (perilaku lama). Validasi: barang ber-batch, satuan dasar,
    Σ = qty, batch milik barang, tanpa batch ganda. Ketersediaan per gudang dinilai saat kirim (BUG-286, baris batch dikunci)."""
    if allocations is None:
        return
    if not allocations:
        return
    if not await _ada_tabel(conn):
        raise _g(422, "ALOKASI_BATCH_BELUM_AKTIF", "Alokasi beberapa batch per baris belum aktif.")
    if product_id is None:
        raise _g(422, "ALOKASI_TANPA_BARANG", "Alokasi batch hanya untuk baris barang.")
    p = await conn.fetchrow("SELECT nama_produk, COALESCE(track_batches, false) AS tb, COALESCE(NULLIF(base_unit, ''), satuan) AS dasar "
                            "FROM products WHERE id = $1 AND tenant_id = $2", product_id, tid)
    nama = p["nama_produk"] if p else "Barang"
    if not p or not p["tb"]:
        raise _g(422, "ALOKASI_BARANG_TANPA_BATCH", f"{nama} tidak dilacak per batch.")
    if unit and p["dasar"] and str(unit).strip().lower() != str(p["dasar"]).strip().lower():
        raise _g(422, "ALOKASI_SATUAN_BUKAN_DASAR", f"Alokasi batch {nama} memakai satuan dasar ({p['dasar']}); ubah satuan baris.")
    al = [a if isinstance(a, Alokasi) else Alokasi(**a) for a in allocations]
    ids = [a.batch_id for a in al]
    if len(set(ids)) != len(ids):
        raise _g(422, "ALOKASI_BATCH_GANDA", f"Batch yang sama muncul dua kali di alokasi {nama}.")
    total = sum((a.quantity for a in al), Decimal("0"))
    if total != Decimal(str(quantity)):
        raise _g(422, "ALOKASI_JUMLAH_TAK_SAMA", f"Alokasi batch {nama} berjumlah {total.normalize():f}, jumlah baris {Decimal(str(quantity)).normalize():f}.")
    milik = {r["id"] for r in await conn.fetch("SELECT id FROM item_batches WHERE tenant_id = $1 AND item_id = $2 AND id = ANY($3::uuid[])",
                                               tid, product_id, ids)}
    asing = [str(i) for i in ids if i not in milik]
    if asing:
        raise _g(422, "ALOKASI_BATCH_BUKAN_BARANG", f"Batch {', '.join(asing)} bukan milik {nama}.")
    baris_id = await conn.fetchval("SELECT id FROM sales_invoice_items WHERE invoice_id = $1 AND line_number = $2", invoice_id, line_number)
    for n, a in enumerate(al, 1):
        await conn.execute(f"INSERT INTO {TABEL} (tenant_id, invoice_item_id, batch_id, quantity, urutan) VALUES ($1, $2, $3, $4, $5)",
                           tid, baris_id, a.batch_id, a.quantity, n)


async def baca(conn, item_ids: list) -> dict:
    """{invoice_item_id: [{batch_id, batch_no, exp_date, quantity}]} untuk respons detail. Tabel belum ada / kosong = {}."""
    if not item_ids or not await _ada_tabel(conn):
        return {}
    out: dict = {}
    for r in await conn.fetch(
            f"""SELECT a.invoice_item_id, a.batch_id, b.batch_number, b.expiry_date, a.quantity FROM {TABEL} a
                LEFT JOIN item_batches b ON b.id = a.batch_id WHERE a.invoice_item_id = ANY($1::uuid[]) ORDER BY a.urutan""", item_ids):
        out.setdefault(r["invoice_item_id"], []).append({
            "batch_id": str(r["batch_id"]), "batch_no": r["batch_number"],
            "exp_date": r["expiry_date"].isoformat() if r["expiry_date"] else None, "quantity": float(r["quantity"])})
    return out


async def pecah_kirim(conn, items_to_fulfill: List[dict]) -> List[dict]:
    """Permintaan kirim tanpa batch pilihan pada baris BER-ALOKASI -> dipecah per batch berurutan, dikurangi yang sudah terkirim per batch.
    Sisa di luar alokasi (mestinya tak ada) tetap satu permintaan tanpa batch (FEFO lama). Baris tanpa alokasi tak berubah."""
    kandidat = [r["invoice_item_id"] for r in items_to_fulfill if not r.get("batch_id") and not r.get("serials")]
    if not kandidat or not await _ada_tabel(conn):
        return items_to_fulfill
    al: dict = {}
    for r in await conn.fetch(f"SELECT invoice_item_id, batch_id, quantity FROM {TABEL} WHERE invoice_item_id = ANY($1::uuid[]) ORDER BY urutan",
                              kandidat):
        al.setdefault(r["invoice_item_id"], []).append(r)
    if not al:
        return items_to_fulfill
    terkirim: dict = {}
    for r in await conn.fetch(
            """SELECT fi.invoice_item_id, fi.batch_id, SUM(fi.quantity) AS q FROM invoice_fulfillment_items fi
               JOIN invoice_fulfillments f ON f.id = fi.fulfillment_id
               WHERE fi.invoice_item_id = ANY($1::uuid[]) AND f.voided_at IS NULL AND f.status <> 'voided' GROUP BY 1, 2""", list(al)):
        terkirim[(r["invoice_item_id"], r["batch_id"])] = Decimal(str(r["q"]))
    hasil = []
    for req in items_to_fulfill:
        daftar = al.get(req["invoice_item_id"]) if not req.get("batch_id") and not req.get("serials") else None
        if not daftar:
            hasil.append(req)
            continue
        sisa_minta = Decimal(str(req["quantity"]))
        for a in daftar:
            if sisa_minta <= 0:
                break
            boleh = Decimal(str(a["quantity"])) - terkirim.get((req["invoice_item_id"], a["batch_id"]), Decimal("0"))
            ambil = min(boleh, sisa_minta)
            if ambil > 0:
                hasil.append({**req, "quantity": ambil, "batch_id": a["batch_id"]})
                sisa_minta -= ambil
        if sisa_minta > 0:
            hasil.append({**req, "quantity": sisa_minta})
    return hasil


async def pecah_fefo(conn, tenant_id: str, warehouse_id, items_to_fulfill: List[dict]) -> List[dict]:
    """BUG-445: permintaan kirim TANPA batch pilihan pada barang ber-batch (track_batches) yang batch FEFO pertamanya tak cukup -> dipecah per batch
    berurutan (FEFO, get_available_batches) selama TOTAL ber-batch cukup; selain itu tak berubah (inti memblok BATCH_STOK_KURANG). Pecahan memakai
    satuan dokumen baris; bila pecahan dasar tak habis dibagi faktor satuan, baris tak dipecah."""
    if warehouse_id is None:
        return items_to_fulfill
    kandidat = [r["invoice_item_id"] for r in items_to_fulfill if not r.get("batch_id") and not r.get("serials")]
    if not kandidat:
        return items_to_fulfill
    info = {r["id"]: r for r in await conn.fetch(
        """SELECT ii.id, ii.item_id, ii.unit FROM sales_invoice_items ii JOIN products p ON p.id = ii.item_id
           WHERE ii.id = ANY($1::uuid[]) AND p.track_batches""", kandidat)}
    if not info:
        return items_to_fulfill
    from fastapi import HTTPException
    from . import satuan_dasar as SD
    hasil = []
    for req in items_to_fulfill:
        i = info.get(req["invoice_item_id"]) if not req.get("batch_id") and not req.get("serials") else None
        if not i:
            hasil.append(req)
            continue
        try:
            q = await SD.qty_dasar(conn, tenant_id, i["item_id"], i["unit"], Decimal(str(req["quantity"])), "konversi")
        except HTTPException:
            hasil.append(req)   # satuan tak terkonversi: biar inti yang menolak dengan kalimatnya
            continue
        qd, f = q["qty_dasar"], q["faktor"]
        rows = await conn.fetch("SELECT * FROM get_available_batches($1, $2, $3, $4, 'FEFO')", tenant_id, i["item_id"], warehouse_id, qd)
        pakai = [(r["batch_id"], Decimal(str(r["quantity_to_use"]))) for r in rows]
        if len(pakai) < 2 or sum(u for _, u in pakai) < qd or any(u % f for _, u in pakai):
            hasil.append(req)
            continue
        total, sisa = Decimal(str(req["quantity"])), Decimal(str(req["quantity"]))
        for n, (bid, u) in enumerate(pakai):
            dok = sisa if n == len(pakai) - 1 else u / f
            hasil.append({**req, "quantity": dok, "batch_id": bid})
            sisa -= dok
    return hasil
