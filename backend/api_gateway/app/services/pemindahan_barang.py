"""Pemindahan Barang antargudang (12 Okt 2026; kontrak R2, putusan MASTER P3/P5/P6/P7). SATU LANGKAH: kirim + terima atomik.

Pola rujukan: NetSuite Inventory Transfer / SAP transfer posting satu-langkah (311) / Accurate Pemindahan Barang.
TANPA JURNAL: akun persediaan ditentukan per PRODUK (products.inventory_account_id), bukan per gudang; WAC = per barang tanpa gudang.
Memindah barang antargudang tak mengubah nilai buku (Law 1/16: GL persediaan tak bergeser; ledger +2 baris per barang, neto nol).

Inti (satu transaksi milik PEMANGGIL, Law 23):
  kunci dokumen + kunci (barang, gudang) berurut (Law 13; anti-oversell lintas dokumen, ironlaws v3.8)
  -> baca header FOR UPDATE -> tolak barang non-stok / batch / serial / WAC nol / stok asal kurang
  -> tulis 2 baris `inventory_ledger` per barang: RELOKASI_KELUAR (gudang asal) + RELOKASI_MASUK (gudang tujuan)
     source_type = RELOKASI_GUDANG, source_id = stock_transfers.id, journal_id NULL SENGAJA
  -> warehouse_stock hanya lewat trigger ledger (Inventory Rule 1: DILARANG UPDATE langsung)
  -> audit (Law 12) -> fase lokasi SO bila PB tertaut SO (info; tak pernah menggerakkan stok).

WAC TIDAK dihitung ulang (P6, perluasan Inventory Rule 3): kedua kaki memakai unit_cost = average_cost = WAC saat itu. Memanggil
record_inventory_inbound akan menghitung ulang dengan pembulatan 2dp -> drift sub-rupiah. `get_weighted_average_cost` membaca
average_cost BARIS TERBARU: kaki relokasi WAJIB membawa WAC sebenarnya, jangan harga beli (jalur lama /items/{id}/stock-transfer
menulis purchase_price ke average_cost -> mencemari WAC penjualan berikutnya; diganti memanggil inti ini).

Void = pembalik ledger kedua kaki (source_type RELOKASI_GUDANG_VOID), ditolak 409 bila stok gudang tujuan sudah terpakai.
Nilai uang memakai Decimal (Law 25); respons float di router.
"""
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from typing import List, Optional
from uuid import UUID

from fastapi import HTTPException
from pydantic import BaseModel, Field, model_validator

from . import so_fase_lokasi
from .so_riwayat import catat_riwayat
from ..utils.tanggal_tenant import tanggal_dokumen

FLAG = "pemindahan_barang"
SUMBER = "RELOKASI_GUDANG"
SUMBER_BATAL = "RELOKASI_GUDANG_VOID"
KELUAR = "RELOKASI_KELUAR"
MASUK = "RELOKASI_MASUK"
_DUA_DP = Decimal("0.01")
_NOL = Decimal("0")


class BarangIn(BaseModel):
    item_id: UUID
    quantity_requested: Decimal = Field(..., gt=0)
    notes: Optional[str] = Field(None, max_length=500)
    # Diabaikan (server memakai WAC & data master); dipertahankan agar klien lama tak 422.
    item_code: Optional[str] = None
    item_name: Optional[str] = None
    unit: Optional[str] = None
    unit_cost: Optional[Decimal] = None
    batch_number: Optional[str] = None
    expiry_date: Optional[date] = None
    serial_numbers: Optional[List[str]] = None


class PemindahanIn(BaseModel):
    from_warehouse_id: UUID
    to_warehouse_id: UUID
    transfer_date: Optional[date] = None
    expected_date: Optional[date] = None
    reference: Optional[str] = Field(None, max_length=100)
    notes: Optional[str] = Field(None, max_length=1000)
    sales_order_id: Optional[UUID] = None
    items: List[BarangIn] = Field(..., min_length=1, max_length=200)

    @model_validator(mode="after")
    def _beda_gudang(self):
        if self.from_warehouse_id == self.to_warehouse_id:
            raise ValueError("Gudang asal dan tujuan harus berbeda")
        return self


def _blok(kode: str, pesan: str, status: int = 409) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": kode, "message": pesan})


async def aktif(conn, tenant_id: str) -> bool:
    return bool(await conn.fetchval(
        "SELECT 1 FROM tenant_features WHERE tenant_id = $1 AND feature = $2 AND enabled", tenant_id, FLAG))


async def pastikan_aktif(conn, tenant_id: str) -> None:
    if not await aktif(conn, tenant_id):
        raise _blok("STOCK_TRANSFER_NOT_AVAILABLE", "Pemindahan barang belum tersedia untuk usaha ini.")


async def _kunci(conn, kunci: str) -> None:
    await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", kunci)


def _nilai(qty: Decimal, wac: Decimal) -> Decimal:
    return (qty * wac).quantize(_DUA_DP, rounding=ROUND_HALF_UP)


async def _gudang(conn, tenant_id: str, gudang_id, peran: str):
    g = await conn.fetchrow("SELECT id, name, is_active FROM warehouses WHERE id = $1 AND tenant_id = $2", gudang_id, tenant_id)
    if not g:
        raise _blok("PB_GUDANG_TAK_ADA", f"Gudang {peran} tidak ditemukan di usaha ini.", 404)
    if not g["is_active"]:
        raise _blok("PB_GUDANG_NONAKTIF", f"Gudang {peran} ({g['name']}) tidak aktif.")
    return g


async def _barang(conn, tenant_id: str, ids: list) -> dict:
    rows = await conn.fetch(
        """SELECT id, item_code, nama_produk, base_unit, COALESCE(track_inventory, false) AS track_inventory,
                  COALESCE(track_batches, false) AS track_batches, COALESCE(track_serial, false) AS track_serial
           FROM products WHERE tenant_id = $1 AND id = ANY($2::uuid[])""", tenant_id, ids)
    by = {r["id"]: r for r in rows}
    for i in ids:
        p = by.get(i)
        if not p:
            raise _blok("PB_BARANG_TAK_ADA", "Barang tidak ditemukan di usaha ini.", 404)
        if not p["track_inventory"]:
            raise _blok("PB_BARANG_NON_STOK", f"{p['nama_produk']} tidak melacak stok; tak ada stok untuk dipindahkan.")
        if p["track_batches"] or p["track_serial"]:
            raise _blok("PB_BARANG_BATCH_SERIAL",
                        f"{p['nama_produk']} dilacak per batch/serial; pemindahan barang jenis ini belum didukung.")
    return by


async def _so_milik_tenant(conn, tenant_id: str, so_id) -> None:
    if so_id is None:
        return
    if not await conn.fetchval("SELECT 1 FROM sales_orders WHERE id = $1 AND tenant_id = $2", so_id, tenant_id):
        raise _blok("PB_SO_TAK_ADA", "Pesanan penjualan tidak ditemukan di usaha ini.", 404)


async def _tulis_baris(conn, transfer_id, by: dict, items: List[BarangIn]) -> None:
    for n, it in enumerate(items, 1):
        p = by[it.item_id]
        await conn.execute(
            """INSERT INTO stock_transfer_items (stock_transfer_id, item_id, item_code, item_name, quantity_requested,
                                                 unit, unit_cost, total_value, line_number, notes)
               VALUES ($1, $2, $3, $4, $5, $6, 0, 0, $7, $8)""",
            transfer_id, it.item_id, p["item_code"], p["nama_produk"], it.quantity_requested, p["base_unit"], n, it.notes)


def _tak_dobel(items: List[BarangIn]) -> None:
    lihat = set()
    for it in items:
        if it.item_id in lihat:
            raise _blok("PB_BARANG_GANDA", "Barang yang sama muncul dua kali; gabungkan jadi satu baris.", 422)
        lihat.add(it.item_id)


async def buat_draf(conn, ctx: dict, body: PemindahanIn) -> UUID:
    tid = ctx["tenant_id"]
    _tak_dobel(body.items)
    await _gudang(conn, tid, body.from_warehouse_id, "asal")
    await _gudang(conn, tid, body.to_warehouse_id, "tujuan")
    by = await _barang(conn, tid, [i.item_id for i in body.items])
    await _so_milik_tenant(conn, tid, body.sales_order_id)
    nomor = await conn.fetchval("SELECT generate_stock_transfer_number($1)", tid)
    tgl = body.transfer_date or await tanggal_dokumen(conn, tid)
    tid_pb = await conn.fetchval(
        """INSERT INTO stock_transfers (tenant_id, transfer_number, transfer_date, from_warehouse_id, to_warehouse_id,
                                        expected_date, reference, notes, sales_order_id, created_by)
           VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10) RETURNING id""",
        tid, nomor, tgl, body.from_warehouse_id, body.to_warehouse_id, body.expected_date, body.reference,
        body.notes, body.sales_order_id, ctx.get("user_id"))
    await _tulis_baris(conn, tid_pb, by, body.items)
    await catat_riwayat(conn, tid, "stock_transfers", tid_pb, nomor, "PEMINDAHAN_BARANG_DIBUAT", ctx.get("user_id"),
                        f"Pemindahan {nomor} dibuat (draf)", {"jumlah_barang": len(body.items)}, source="api:stock_transfers.create")
    return tid_pb


async def _draf_terkunci(conn, tid: str, transfer_id):
    await _kunci(conn, f"STOCK_TRANSFER:{tid}:{transfer_id}")
    h = await conn.fetchrow("SELECT * FROM stock_transfers WHERE id = $1 AND tenant_id = $2 FOR UPDATE", transfer_id, tid)
    if not h:
        raise _blok("PB_TAK_ADA", "Pemindahan barang tidak ditemukan.", 404)
    return h


async def ubah_draf(conn, ctx: dict, transfer_id, body: PemindahanIn) -> None:
    tid = ctx["tenant_id"]
    h = await _draf_terkunci(conn, tid, transfer_id)
    if h["status"] != "draft":
        raise _blok("PB_STATUS_TIDAK_BOLEH", f"Pemindahan {h['transfer_number']} berstatus {h['status']}; hanya draf yang bisa diubah.")
    _tak_dobel(body.items)
    await _gudang(conn, tid, body.from_warehouse_id, "asal")
    await _gudang(conn, tid, body.to_warehouse_id, "tujuan")
    by = await _barang(conn, tid, [i.item_id for i in body.items])
    await _so_milik_tenant(conn, tid, body.sales_order_id)
    await conn.execute(
        """UPDATE stock_transfers SET from_warehouse_id = $3, to_warehouse_id = $4, transfer_date = COALESCE($5, transfer_date),
               expected_date = $6, reference = $7, notes = $8, sales_order_id = $9, updated_at = NOW()
           WHERE id = $1 AND tenant_id = $2""",
        transfer_id, tid, body.from_warehouse_id, body.to_warehouse_id, body.transfer_date, body.expected_date,
        body.reference, body.notes, body.sales_order_id)
    await conn.execute("DELETE FROM stock_transfer_items WHERE stock_transfer_id = $1", transfer_id)
    await _tulis_baris(conn, transfer_id, by, body.items)
    await catat_riwayat(conn, tid, "stock_transfers", transfer_id, h["transfer_number"], "PEMINDAHAN_BARANG_DIUBAH",
                        ctx.get("user_id"), f"Draf pemindahan {h['transfer_number']} diubah", {"jumlah_barang": len(body.items)},
                        source="api:stock_transfers.update")


async def hapus_draf(conn, ctx: dict, transfer_id) -> None:
    tid = ctx["tenant_id"]
    h = await _draf_terkunci(conn, tid, transfer_id)
    if h["status"] != "draft":
        raise _blok("PB_STATUS_TIDAK_BOLEH", f"Pemindahan {h['transfer_number']} berstatus {h['status']}; hanya draf yang bisa dihapus.")
    await conn.execute("DELETE FROM stock_transfers WHERE id = $1 AND tenant_id = $2", transfer_id, tid)
    await catat_riwayat(conn, tid, "stock_transfers", transfer_id, h["transfer_number"], "PEMINDAHAN_BARANG_DIHAPUS",
                        ctx.get("user_id"), f"Draf pemindahan {h['transfer_number']} dihapus", None, source="api:stock_transfers.delete")


async def batalkan_draf(conn, ctx: dict, transfer_id, alasan: str) -> None:
    tid = ctx["tenant_id"]
    h = await _draf_terkunci(conn, tid, transfer_id)
    if h["status"] == "cancelled":
        return
    if h["status"] != "draft":
        raise _blok("PB_STATUS_TIDAK_BOLEH",
                    f"Pemindahan {h['transfer_number']} berstatus {h['status']}; yang sudah selesai dibatalkan lewat void.")
    await conn.execute(
        "UPDATE stock_transfers SET status = 'cancelled', cancelled_at = NOW(), cancelled_by = $3, cancel_reason = $4, updated_at = NOW() "
        "WHERE id = $1 AND tenant_id = $2", transfer_id, tid, ctx.get("user_id"), alasan)
    await catat_riwayat(conn, tid, "stock_transfers", transfer_id, h["transfer_number"], "PEMINDAHAN_BARANG_DIBATALKAN",
                        ctx.get("user_id"), f"Pemindahan {h['transfer_number']} dibatalkan: {alasan}", {"alasan": alasan},
                        source="api:stock_transfers.cancel")


async def _saldo_produk(conn, tid: str, product_id) -> Decimal:
    return Decimal(str(await conn.fetchval(
        "SELECT COALESCE(SUM(quantity_in - quantity_out), 0) FROM inventory_ledger WHERE tenant_id = $1 AND product_id = $2",
        tid, product_id)))


async def _tulis_ledger(conn, tid: str, user_id, *, product_id, kode, nama, tipe: str, sumber: str, tanggal, transfer_id,
                        nomor: str, qty: Decimal, masuk: bool, saldo: Decimal, unit_cost: Decimal, total: Decimal,
                        wac: Decimal, gudang_id, catatan: str) -> None:
    await conn.execute(
        """INSERT INTO inventory_ledger (tenant_id, product_id, product_code, product_name, movement_type, movement_date,
                                         source_type, source_id, source_number, quantity_in, quantity_out, quantity_balance,
                                         unit_cost, total_cost, average_cost, warehouse_id, created_by, notes)
           VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17, $18)""",
        tid, product_id, kode, nama, tipe, tanggal, sumber, transfer_id, nomor,
        qty if masuk else _NOL, _NOL if masuk else qty, saldo, unit_cost, total, wac, gudang_id, user_id, catatan)


async def _fase_so(conn, ctx: dict, h, ke_fase: Optional[str], gudang_id) -> Optional[str]:
    """Pertemuan dengan fase lokasi SO (P7). Informasi saja: blok SO (tertutup/gudang) TIDAK menggagalkan pemindahan.
    -> teks catatan bila fase tak diubah, selain itu None."""
    so_id = h["sales_order_id"]
    if so_id is None:
        return None
    r = await so_fase_lokasi.rencana(conn, ctx, so_id, ke_fase, gudang_id, kunci=True)
    if r["blocks"]:
        return f"Fase lokasi pesanan tidak diubah: {r['blocks'][0]['message']}"
    if r["berubah"]:
        await so_fase_lokasi.terapkan(conn, ctx, so_id, ke_fase, gudang_id)
    return None


async def selesaikan(conn, ctx: dict, transfer_id) -> dict:
    """draft -> completed. Idempoten: yang sudah completed -> {'ulang': True} tanpa tulis."""
    tid, uid = ctx["tenant_id"], ctx.get("user_id")
    h = await _draf_terkunci(conn, tid, transfer_id)
    if h["status"] == "completed":
        return {"ulang": True, "catatan_fase": None}
    if h["status"] != "draft":
        raise _blok("PB_STATUS_TIDAK_BOLEH", f"Pemindahan {h['transfer_number']} berstatus {h['status']}; hanya draf yang bisa diselesaikan.")
    asal = await _gudang(conn, tid, h["from_warehouse_id"], "asal")
    tujuan = await _gudang(conn, tid, h["to_warehouse_id"], "tujuan")
    items = await conn.fetch(
        "SELECT * FROM stock_transfer_items WHERE stock_transfer_id = $1 ORDER BY line_number, id", transfer_id)
    if not items:
        raise _blok("PB_TANPA_BARANG", "Pemindahan tak punya baris barang.", 422)
    by = await _barang(conn, tid, [i["item_id"] for i in items])
    # Kunci (barang, gudang) berurut -> tanpa deadlock lintas dokumen (Law 13 + ironlaws v3.8).
    for p, w in sorted({(str(i["item_id"]), str(g)) for i in items for g in (h["from_warehouse_id"], h["to_warehouse_id"])}):
        await _kunci(conn, f"STOCK:{p}:{w}")
    hari = await tanggal_dokumen(conn, tid)
    total = _NOL
    for it in items:
        p = by[it["item_id"]]
        qty = Decimal(str(it["quantity_requested"]))
        wac = Decimal(str(await conn.fetchval("SELECT get_weighted_average_cost($1, $2)", tid, it["item_id"]) or 0))
        if wac <= _NOL:
            raise _blok("PB_WAC_NOL", f"Biaya rata-rata {p['nama_produk']} belum ada (0); tak bisa memindahkan tanpa nilai.")
        ada = Decimal(str(await conn.fetchval(
            "SELECT COALESCE((SELECT available_quantity FROM warehouse_stock WHERE tenant_id = $1 AND warehouse_id = $2 "
            "AND item_id = $3 FOR UPDATE), 0)", tid, h["from_warehouse_id"], it["item_id"])))
        if ada < qty:
            raise _blok("PB_STOK_TIDAK_CUKUP",
                        f"Stok {p['nama_produk']} di {asal['name']} hanya {ada:f}, diminta {qty:f}.")
        nilai = _nilai(qty, wac)
        saldo = await _saldo_produk(conn, tid, it["item_id"])
        kw = dict(product_id=it["item_id"], kode=p["item_code"], nama=p["nama_produk"], sumber=SUMBER, tanggal=hari,
                  transfer_id=transfer_id, nomor=h["transfer_number"], qty=qty, unit_cost=wac, total=nilai, wac=wac)
        await _tulis_ledger(conn, tid, uid, tipe=KELUAR, masuk=False, saldo=saldo - qty, gudang_id=h["from_warehouse_id"],
                            catatan=f"Pemindahan ke {tujuan['name']}", **kw)
        await _tulis_ledger(conn, tid, uid, tipe=MASUK, masuk=True, saldo=saldo, gudang_id=h["to_warehouse_id"],
                            catatan=f"Pemindahan dari {asal['name']}", **kw)
        # quantity_variance = kolom GENERATED (shipped - received): TIDAK ditulis (galat nyata di harness 12 Okt).
        await conn.execute(
            "UPDATE stock_transfer_items SET quantity_shipped = $2, quantity_received = $2, "
            "unit_cost = $3, total_value = $4 WHERE id = $1", it["id"], qty, wac, nilai)
        total += nilai
    await conn.execute(
        "UPDATE stock_transfers SET status = 'completed', shipped_date = $3, received_date = $3, shipped_by = $4, received_by = $4, "
        "updated_at = NOW() WHERE id = $1 AND tenant_id = $2", transfer_id, tid, hari, uid)
    await catat_riwayat(conn, tid, "stock_transfers", transfer_id, h["transfer_number"], "PEMINDAHAN_BARANG_SELESAI", uid,
                        f"Pemindahan {h['transfer_number']}: {asal['name']} -> {tujuan['name']} ({len(items)} barang)",
                        {"dari": asal["name"], "ke": tujuan["name"], "barang": len(items), "nilai": str(total)},
                        source="api:stock_transfers.complete")
    catatan = await _fase_so(conn, ctx, h, "tersedia", h["to_warehouse_id"])
    return {"ulang": False, "catatan_fase": catatan}


async def void(conn, ctx: dict, transfer_id, alasan: str) -> dict:
    """completed -> voided: pembalik ledger kedua kaki. Idempoten untuk yang sudah voided."""
    tid, uid = ctx["tenant_id"], ctx.get("user_id")
    h = await _draf_terkunci(conn, tid, transfer_id)
    if h["status"] == "voided":
        return {"ulang": True, "catatan_fase": None}
    if h["status"] != "completed":
        raise _blok("PB_STATUS_TIDAK_BOLEH", f"Pemindahan {h['transfer_number']} berstatus {h['status']}; hanya yang selesai bisa di-void.")
    asli = await conn.fetch(
        "SELECT * FROM inventory_ledger WHERE tenant_id = $1 AND source_type = $2 AND source_id = $3 ORDER BY created_at, id",
        tid, SUMBER, transfer_id)
    if not asli:
        raise _blok("PB_LEDGER_TAK_ADA", "Baris kartu stok pemindahan ini tidak ditemukan; tak bisa dibalik.")
    if await conn.fetchval(
            "SELECT 1 FROM inventory_ledger WHERE tenant_id = $1 AND source_type = $2 AND source_id = $3 LIMIT 1",
            tid, SUMBER_BATAL, transfer_id):
        raise _blok("PB_SUDAH_DIBALIK", "Kartu stok pemindahan ini sudah pernah dibalik.")
    for p, w in sorted({(str(r["product_id"]), str(r["warehouse_id"])) for r in asli}):
        await _kunci(conn, f"STOCK:{p}:{w}")
    nama_gudang = {r["id"]: r["name"] for r in await conn.fetch(
        "SELECT id, name FROM warehouses WHERE tenant_id = $1 AND id = ANY($2::uuid[])", tid,
        [h["from_warehouse_id"], h["to_warehouse_id"]])}
    hari = await tanggal_dokumen(conn, tid)
    # Kaki masuk asli (gudang tujuan) dibalik jadi KELUAR lebih dulu: stok tujuan harus cukup (dicek eksplisit -> 409 jujur).
    urut = sorted(asli, key=lambda r: 0 if Decimal(str(r["quantity_in"])) > _NOL else 1)
    for r in urut:
        masuk_asli = Decimal(str(r["quantity_in"])) > _NOL
        qty = Decimal(str(r["quantity_in"] if masuk_asli else r["quantity_out"]))
        if masuk_asli:
            ada = Decimal(str(await conn.fetchval(
                "SELECT COALESCE((SELECT available_quantity FROM warehouse_stock WHERE tenant_id = $1 AND warehouse_id = $2 "
                "AND item_id = $3 FOR UPDATE), 0)", tid, r["warehouse_id"], r["product_id"])))
            if ada < qty:
                raise _blok("PB_STOK_TUJUAN_TERPAKAI",
                            f"Stok {r['product_name']} di {nama_gudang.get(r['warehouse_id'], 'gudang tujuan')} tinggal {ada:f}, "
                            f"kurang untuk membalik {qty:f}; pemindahan tak bisa di-void.")
        wac_kini = Decimal(str(await conn.fetchval("SELECT get_weighted_average_cost($1, $2)", tid, r["product_id"]) or 0))
        saldo = await _saldo_produk(conn, tid, r["product_id"])
        await _tulis_ledger(
            conn, tid, uid, product_id=r["product_id"], kode=r["product_code"], nama=r["product_name"], tipe=SUMBER_BATAL,
            sumber=SUMBER_BATAL, tanggal=hari, transfer_id=transfer_id, nomor=h["transfer_number"], qty=qty,
            masuk=not masuk_asli, saldo=saldo + (qty if not masuk_asli else -qty),
            unit_cost=Decimal(str(r["unit_cost"])), total=Decimal(str(r["total_cost"])), wac=wac_kini,
            gudang_id=r["warehouse_id"], catatan=f"Void pemindahan {h['transfer_number']}: {alasan}")
    await conn.execute(
        "UPDATE stock_transfers SET status = 'voided', voided_at = NOW(), voided_by = $3, void_reason = $4, updated_at = NOW() "
        "WHERE id = $1 AND tenant_id = $2", transfer_id, tid, uid, alasan)
    await catat_riwayat(conn, tid, "stock_transfers", transfer_id, h["transfer_number"], "PEMINDAHAN_BARANG_DI_VOID", uid,
                        f"Pemindahan {h['transfer_number']} di-void: {alasan}", {"alasan": alasan}, source="api:stock_transfers.void")
    catatan = None
    if h["sales_order_id"] is not None:
        # Fase hanya DIKOSONGKAN bila masih persis hasil pemindahan ini (tersedia @ gudang tujuan); selain itu tak disentuh.
        so = await conn.fetchrow("SELECT fase_lokasi, fase_gudang_id FROM sales_orders WHERE id = $1 AND tenant_id = $2",
                                 h["sales_order_id"], tid)
        if so and so["fase_lokasi"] == "tersedia" and so["fase_gudang_id"] == h["to_warehouse_id"]:
            catatan = await _fase_so(conn, ctx, h, None, None)
    return {"ulang": False, "catatan_fase": catatan}
