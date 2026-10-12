"""Pemindahan Barang antargudang (12 Okt 2026; kontrak R2, putusan MASTER P3/P5/P6/P7). SATU LANGKAH: kirim + terima atomik.

Pola rujukan: NetSuite Inventory Transfer / SAP transfer posting satu-langkah (311) / Accurate Pemindahan Barang.
TANPA JURNAL: akun persediaan ditentukan per PRODUK (products.inventory_account_id), bukan per gudang; WAC = per barang tanpa gudang.
Memindah barang antargudang tak mengubah nilai buku (Law 1/16: GL persediaan tak bergeser; ledger +2 baris per barang, neto nol).

SATU PENENTU (v2, permintaan FE "Tinjau = angka server"): `_rencana` / `_rencana_void` / `_rencana_batal` menghitung baris, nilai (WAC),
stok sebelum/sesudah per gudang dan SEMUA blok. Rute pratinjau memakainya TANPA menulis; jalur tulis memakai fungsi YANG SAMA
(kunci=True: baris stok dikunci FOR UPDATE) lalu menulis dari hasilnya -> angka pratinjau == angka tulis, tak ada logika ganda.

Inti tulis (satu transaksi milik PEMANGGIL, Law 23):
  kunci dokumen + kunci (barang, gudang) berurut (Law 13; anti-oversell lintas dokumen, ironlaws v3.8)
  -> baca header FOR UPDATE -> rencana (blok pertama = galat) -> 2 baris `inventory_ledger` per barang: RELOKASI_KELUAR (asal) +
     RELOKASI_MASUK (tujuan); source_type RELOKASI_GUDANG, source_id = stock_transfers.id, journal_id NULL SENGAJA
  -> warehouse_stock hanya lewat trigger ledger (Inventory Rule 1: DILARANG UPDATE langsung)
  -> audit (Law 12) -> fase lokasi SO bila PB tertaut SO (info; tak pernah menggerakkan stok).

WAC TIDAK dihitung ulang (P6, perluasan Inventory Rule 3): kedua kaki memakai unit_cost = average_cost = WAC saat itu.
`get_weighted_average_cost` membaca average_cost BARIS TERBARU: kaki relokasi WAJIB membawa WAC sebenarnya (jalur lama
/items/{id}/stock-transfer menulis harga beli -> mencemari WAC; kini memanggil inti ini).
Void = pembalik ledger kedua kaki (RELOKASI_GUDANG_VOID), ditolak 409 bila stok gudang tujuan sudah terpakai.
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


class BarangPratinjau(BaseModel):
    """Pratinjau MENERIMA badan tak lengkap/tak sah dan menjawab 200 dengan SEMUA blok (bukan 422 per kolom)."""
    item_id: Optional[UUID] = None
    quantity_requested: Optional[Decimal] = None
    notes: Optional[str] = None
    item_code: Optional[str] = None
    item_name: Optional[str] = None
    unit: Optional[str] = None
    unit_cost: Optional[Decimal] = None


class PratinjauIn(BaseModel):
    from_warehouse_id: Optional[UUID] = None
    to_warehouse_id: Optional[UUID] = None
    transfer_date: Optional[date] = None
    expected_date: Optional[date] = None
    reference: Optional[str] = None
    notes: Optional[str] = None
    sales_order_id: Optional[UUID] = None
    items: List[BarangPratinjau] = Field(default_factory=list)


def _blok(kode: str, pesan: str, status: int = 409) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": kode, "message": pesan})


def angkat_blok_pertama(rencana: dict) -> None:
    if rencana["blocks"]:
        b = rencana["blocks"][0]
        raise HTTPException(status_code=b["status"], detail={"code": b["code"], "message": b["message"]})


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


def _s(x) -> Optional[str]:
    return str(x) if x is not None else None


async def _stok(conn, tid: str, gudang_id, item_id, kunci: bool) -> Decimal:
    q = ("SELECT COALESCE((SELECT available_quantity FROM warehouse_stock WHERE tenant_id = $1 AND warehouse_id = $2 "
         "AND item_id = $3" + (" FOR UPDATE" if kunci else "") + "), 0)")
    return Decimal(str(await conn.fetchval(q, tid, gudang_id, item_id)))


# ============================================================================
# PENENTU (satu sumber angka: pratinjau == tulis)
# ============================================================================
async def _rencana(conn, tid: str, dari_id, ke_id, baris: list, so_id, kunci: bool = False, ctx: Optional[dict] = None) -> dict:
    """baris: [{"item_id": UUID|None, "qty": Decimal|None, "notes": str|None}]. Mengumpulkan SEMUA blok.
    -> {blocks, lines, dari, ke, total_quantity, total_value, sales_order, catatan_fase}. Tanpa tulis (kunci=True hanya mengunci baris stok)."""
    blocks: list = []

    def blok(kode, pesan, status=409, item_id=None):
        blocks.append({"code": kode, "message": pesan, "status": status, "item_id": _s(item_id)})

    async def gud(gid, peran):
        if gid is None:
            blok(f"PB_GUDANG_{peran.upper()}_WAJIB", f"Gudang {peran} wajib dipilih.", 422)
            return None
        g = await conn.fetchrow("SELECT id, name, is_active FROM warehouses WHERE id = $1 AND tenant_id = $2", gid, tid)
        if not g:
            blok("PB_GUDANG_TAK_ADA", f"Gudang {peran} tidak ditemukan di usaha ini.", 404)
            return None
        if not g["is_active"]:
            blok("PB_GUDANG_NONAKTIF", f"Gudang {peran} ({g['name']}) tidak aktif.")
        return g

    asal = await gud(dari_id, "asal")
    tujuan = await gud(ke_id, "tujuan")
    if dari_id is not None and dari_id == ke_id:
        blok("PB_GUDANG_SAMA", "Gudang asal dan tujuan harus berbeda.", 422)
    if not baris:
        blok("PB_TANPA_BARANG", "Pemindahan tak punya baris barang.", 422)

    ids = [b["item_id"] for b in baris if b.get("item_id") is not None]
    by = {}
    if ids:
        for r in await conn.fetch(
                """SELECT id, item_code, nama_produk, base_unit, COALESCE(track_inventory, false) AS track_inventory,
                          COALESCE(track_batches, false) AS track_batches, COALESCE(track_serial, false) AS track_serial
                   FROM products WHERE tenant_id = $1 AND id = ANY($2::uuid[])""", tid, ids):
            by[r["id"]] = r

    lines, lihat = [], set()
    total_q = total_v = _NOL
    # Kunci stok urut (barang, gudang) -> tanpa deadlock; hanya jalur tulis.
    urut = sorted((b for b in baris if b.get("item_id") is not None), key=lambda b: str(b["item_id"]))
    for b in urut:
        iid, qty = b["item_id"], b.get("qty")
        p = by.get(iid)
        if iid in lihat:
            blok("PB_BARANG_GANDA", "Barang yang sama muncul dua kali; gabungkan jadi satu baris.", 422, iid)
            continue
        lihat.add(iid)
        if not p:
            blok("PB_BARANG_TAK_ADA", "Barang tidak ditemukan di usaha ini.", 404, iid)
            continue
        nama = p["nama_produk"]
        if qty is None or qty <= _NOL:
            blok("PB_QTY_TIDAK_SAH", f"Jumlah {nama} harus lebih dari 0.", 422, iid)
            continue
        if not p["track_inventory"]:
            blok("PB_BARANG_NON_STOK", f"{nama} tidak melacak stok; tak ada stok untuk dipindahkan.", 409, iid)
            continue
        if p["track_batches"] or p["track_serial"]:
            blok("PB_BARANG_BATCH_SERIAL", f"{nama} dilacak per batch/serial; pemindahan barang jenis ini belum didukung.", 409, iid)
            continue
        wac = Decimal(str(await conn.fetchval("SELECT get_weighted_average_cost($1, $2)", tid, iid) or 0))
        if kunci and asal and tujuan:
            for w in sorted({str(dari_id), str(ke_id)}):
                await _kunci(conn, f"STOCK:{iid}:{w}")
        ada_asal = await _stok(conn, tid, dari_id, iid, kunci) if asal else None
        ada_tuj = await _stok(conn, tid, ke_id, iid, False) if tujuan else None
        if wac <= _NOL:
            blok("PB_WAC_NOL", f"Biaya rata-rata {nama} belum ada (0); tak bisa memindahkan tanpa nilai.", 409, iid)
        if ada_asal is not None and ada_asal < qty:
            blok("PB_STOK_TIDAK_CUKUP", f"Stok {nama} di {asal['name']} hanya {ada_asal:f}, diminta {qty:f}.", 409, iid)
        nilai = _nilai(qty, wac)
        total_q += qty
        total_v += nilai
        lines.append({
            "item_id": iid, "name": nama, "unit": p["base_unit"], "item_code": p["item_code"], "quantity": qty,
            "available_from": ada_asal, "available_to": ada_tuj, "unit_cost": wac, "value": nilai,
            "from_before": ada_asal, "from_after": (ada_asal - qty) if ada_asal is not None else None,
            "to_before": ada_tuj, "to_after": (ada_tuj + qty) if ada_tuj is not None else None,
            "notes": b.get("notes"),
        })

    so, catatan = None, None
    if so_id is not None:
        order = await conn.fetchrow("SELECT id, order_number FROM sales_orders WHERE id = $1 AND tenant_id = $2", so_id, tid)
        if not order:
            blok("PB_SO_TAK_ADA", "Pesanan penjualan tidak ditemukan di usaha ini.", 404)
        else:
            so = {"id": str(order["id"]), "number": order["order_number"], "fase_sesudah": None}
            if tujuan and ctx is not None:
                r = await so_fase_lokasi.rencana(conn, ctx, so_id, "tersedia", ke_id, kunci=False)
                if r["blocks"]:
                    catatan = f"Fase lokasi pesanan tidak diubah: {r['blocks'][0]['message']}"
                else:
                    so["fase_sesudah"] = {"fase_lokasi": "tersedia", "fase_gudang_id": str(ke_id),
                                          "teks": so_fase_lokasi.teks_fase("tersedia", tujuan["name"]), "berubah": r["berubah"]}
    return {
        "blocks": blocks, "lines": lines,
        "dari": {"id": _s(dari_id), "name": asal["name"] if asal else None},
        "ke": {"id": _s(ke_id), "name": tujuan["name"] if tujuan else None},
        "total_quantity": total_q, "total_value": total_v, "sales_order": so, "catatan_fase": catatan,
    }


def _baris_dari_isi(isi) -> list:
    return [{"item_id": i.item_id, "qty": i.quantity_requested, "notes": i.notes} for i in isi.items]


def payload_dari(isi) -> dict:
    return {
        "from_warehouse_id": _s(isi.from_warehouse_id), "to_warehouse_id": _s(isi.to_warehouse_id),
        "transfer_date": isi.transfer_date.isoformat() if isi.transfer_date else None,
        "expected_date": isi.expected_date.isoformat() if isi.expected_date else None,
        "reference": isi.reference, "notes": isi.notes, "sales_order_id": _s(isi.sales_order_id),
        "items": [{"item_id": _s(i.item_id), "quantity_requested": str(i.quantity_requested) if i.quantity_requested is not None else None,
                   "notes": i.notes} for i in isi.items],
    }


async def pratinjau_baru(conn, ctx: dict, isi: PratinjauIn) -> dict:
    """Tinjau DRAF/SIMPAN-SELESAI baru: nol tulis, 200 walau diblok, SEMUA blok."""
    r = await _rencana(conn, ctx["tenant_id"], isi.from_warehouse_id, isi.to_warehouse_id, _baris_dari_isi(isi),
                       isi.sales_order_id, kunci=False, ctx=ctx)
    r["payload"] = payload_dari(isi)
    r["ok"] = not r["blocks"]
    return r


async def _header(conn, tid: str, transfer_id, kunci: bool = False):
    h = await conn.fetchrow("SELECT * FROM stock_transfers WHERE id = $1 AND tenant_id = $2" + (" FOR UPDATE" if kunci else ""),
                            transfer_id, tid)
    if not h:
        raise _blok("PB_TAK_ADA", "Pemindahan barang tidak ditemukan.", 404)
    return h


async def pratinjau_selesai(conn, ctx: dict, transfer_id) -> dict:
    tid = ctx["tenant_id"]
    h = await _header(conn, tid, transfer_id)
    items = await conn.fetch("SELECT * FROM stock_transfer_items WHERE stock_transfer_id = $1 ORDER BY line_number, id", transfer_id)
    r = await _rencana(conn, tid, h["from_warehouse_id"], h["to_warehouse_id"],
                       [{"item_id": i["item_id"], "qty": Decimal(str(i["quantity_requested"])), "notes": i["notes"]} for i in items],
                       h["sales_order_id"], kunci=False, ctx=ctx)
    if h["status"] not in ("draft", "completed"):
        r["blocks"].insert(0, {"code": "PB_STATUS_TIDAK_BOLEH", "status": 409, "item_id": None,
                               "message": f"Pemindahan {h['transfer_number']} berstatus {h['status']}; hanya draf yang bisa diselesaikan."})
    r["ulang"] = h["status"] == "completed"
    if r["ulang"]:
        r["blocks"] = []
    r["ok"] = not r["blocks"]
    r["transfer_number"] = h["transfer_number"]
    r["payload"] = {"transfer_id": str(transfer_id)}
    return r


async def _rencana_void(conn, tid: str, h, kunci: bool) -> dict:
    blocks: list = []
    lines: list = []
    asli = []
    if h["status"] == "voided":
        return {"blocks": [], "lines": [], "ulang": True, "asli": [], "dari": None, "ke": None,
                "total_quantity": _NOL, "total_value": _NOL, "sales_order": None, "catatan_fase": None}
    if h["status"] != "completed":
        blocks.append({"code": "PB_STATUS_TIDAK_BOLEH", "status": 409, "item_id": None,
                       "message": f"Pemindahan {h['transfer_number']} berstatus {h['status']}; hanya yang selesai bisa di-void."})
    else:
        asli = await conn.fetch(
            "SELECT * FROM inventory_ledger WHERE tenant_id = $1 AND source_type = $2 AND source_id = $3 ORDER BY created_at, id",
            tid, SUMBER, h["id"])
        if not asli:
            blocks.append({"code": "PB_LEDGER_TAK_ADA", "status": 409, "item_id": None,
                           "message": "Baris kartu stok pemindahan ini tidak ditemukan; tak bisa dibalik."})
        elif await conn.fetchval(
                "SELECT 1 FROM inventory_ledger WHERE tenant_id = $1 AND source_type = $2 AND source_id = $3 LIMIT 1",
                tid, SUMBER_BATAL, h["id"]):
            blocks.append({"code": "PB_SUDAH_DIBALIK", "status": 409, "item_id": None,
                           "message": "Kartu stok pemindahan ini sudah pernah dibalik."})
    nama_gudang = {r["id"]: r["name"] for r in await conn.fetch(
        "SELECT id, name FROM warehouses WHERE tenant_id = $1 AND id = ANY($2::uuid[])", tid,
        [h["from_warehouse_id"], h["to_warehouse_id"]])}
    per_barang: dict = {}
    for r in asli:
        masuk_asli = Decimal(str(r["quantity_in"])) > _NOL
        qty = Decimal(str(r["quantity_in"] if masuk_asli else r["quantity_out"]))
        d = per_barang.setdefault(r["product_id"], {"nama": r["product_name"], "qty": qty, "nilai": Decimal(str(r["total_cost"])),
                                                   "unit_cost": Decimal(str(r["unit_cost"]))})
        d["qty"] = qty
    for pid, d in per_barang.items():
        ada_tuj = await _stok(conn, tid, h["to_warehouse_id"], pid, kunci)
        ada_asal = await _stok(conn, tid, h["from_warehouse_id"], pid, False)
        if ada_tuj < d["qty"]:
            blocks.append({"code": "PB_STOK_TUJUAN_TERPAKAI", "status": 409, "item_id": str(pid),
                           "message": f"Stok {d['nama']} di {nama_gudang.get(h['to_warehouse_id'], 'gudang tujuan')} tinggal "
                                      f"{ada_tuj:f}, kurang untuk membalik {d['qty']:f}; pemindahan tak bisa di-void."})
        lines.append({"item_id": pid, "name": d["nama"], "unit": None, "quantity": d["qty"], "available_from": ada_tuj,
                      "available_to": ada_asal, "unit_cost": d["unit_cost"], "value": d["nilai"],
                      "from_before": ada_tuj, "from_after": ada_tuj - d["qty"], "to_before": ada_asal, "to_after": ada_asal + d["qty"],
                      "keterangan": "stok gudang tujuan berkurang, gudang asal kembali"})
    return {"blocks": blocks, "lines": lines, "ulang": False, "asli": list(asli),
            "dari": {"id": _s(h["to_warehouse_id"]), "name": nama_gudang.get(h["to_warehouse_id"])},
            "ke": {"id": _s(h["from_warehouse_id"]), "name": nama_gudang.get(h["from_warehouse_id"])},
            "total_quantity": sum((d["qty"] for d in per_barang.values()), _NOL),
            "total_value": sum((d["nilai"] for d in per_barang.values()), _NOL), "sales_order": None, "catatan_fase": None}


async def pratinjau_void(conn, ctx: dict, transfer_id, alasan: Optional[str]) -> dict:
    tid = ctx["tenant_id"]
    h = await _header(conn, tid, transfer_id)
    r = await _rencana_void(conn, tid, h, kunci=False)
    if not (alasan or "").strip():
        r["blocks"].append({"code": "PB_ALASAN_WAJIB", "status": 422, "item_id": None, "message": "Alasan void wajib diisi."})
    r.pop("asli", None)
    r["ok"] = not r["blocks"]
    r["transfer_number"] = h["transfer_number"]
    r["payload"] = {"transfer_id": str(transfer_id), "reason": alasan}
    return r


async def pratinjau_batal(conn, ctx: dict, transfer_id, alasan: Optional[str]) -> dict:
    tid = ctx["tenant_id"]
    h = await _header(conn, tid, transfer_id)
    blocks = []
    ulang = h["status"] == "cancelled"
    if h["status"] not in ("draft", "cancelled"):
        blocks.append({"code": "PB_STATUS_TIDAK_BOLEH", "status": 409, "item_id": None,
                       "message": f"Pemindahan {h['transfer_number']} berstatus {h['status']}; yang sudah selesai dibatalkan lewat void."})
    if not (alasan or "").strip():
        blocks.append({"code": "PB_ALASAN_WAJIB", "status": 422, "item_id": None, "message": "Alasan pembatalan wajib diisi."})
    return {"ok": not blocks, "blocks": blocks, "ulang": ulang, "lines": [], "transfer_number": h["transfer_number"],
            "total_quantity": Decimal(str(h.get("total_quantity") or 0)), "total_value": _NOL, "sales_order": None, "catatan_fase": None,
            "dari": None, "ke": None, "payload": {"transfer_id": str(transfer_id), "reason": alasan}}


# ============================================================================
# TULIS
# ============================================================================
async def _tulis_baris(conn, transfer_id, by: dict, items: List[BarangIn]) -> None:
    for n, it in enumerate(items, 1):
        p = by[it.item_id]
        await conn.execute(
            """INSERT INTO stock_transfer_items (stock_transfer_id, item_id, item_code, item_name, quantity_requested,
                                                 unit, unit_cost, total_value, line_number, notes)
               VALUES ($1, $2, $3, $4, $5, $6, 0, 0, $7, $8)""",
            transfer_id, it.item_id, p["item_code"], p["nama_produk"], it.quantity_requested, p["base_unit"], n, it.notes)


async def _validasi_isi(conn, tid: str, isi: PemindahanIn) -> dict:
    """Validasi struktural untuk draf (tanpa kunci stok): blok pertama = galat tulis; mengembalikan peta barang."""
    r = await _rencana(conn, tid, isi.from_warehouse_id, isi.to_warehouse_id, _baris_dari_isi(isi), isi.sales_order_id, kunci=False)
    # Draf boleh melebihi stok / WAC nol saat ini (dicek lagi saat diselesaikan): hanya blok struktural yang menghalangi.
    struktural = [b for b in r["blocks"] if b["code"] not in ("PB_STOK_TIDAK_CUKUP", "PB_WAC_NOL")]
    if struktural:
        b = struktural[0]
        raise HTTPException(status_code=b["status"], detail={"code": b["code"], "message": b["message"]})
    rows = await conn.fetch(
        "SELECT id, item_code, nama_produk, base_unit FROM products WHERE tenant_id = $1 AND id = ANY($2::uuid[])",
        tid, [i.item_id for i in isi.items])
    return {x["id"]: x for x in rows}


async def buat_draf(conn, ctx: dict, body: PemindahanIn) -> UUID:
    tid = ctx["tenant_id"]
    by = await _validasi_isi(conn, tid, body)
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
    return await _header(conn, tid, transfer_id, kunci=True)


async def ubah_draf(conn, ctx: dict, transfer_id, body: PemindahanIn) -> None:
    tid = ctx["tenant_id"]
    h = await _draf_terkunci(conn, tid, transfer_id)
    if h["status"] != "draft":
        raise _blok("PB_STATUS_TIDAK_BOLEH", f"Pemindahan {h['transfer_number']} berstatus {h['status']}; hanya draf yang bisa diubah.")
    by = await _validasi_isi(conn, tid, body)
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
    items = await conn.fetch(
        "SELECT * FROM stock_transfer_items WHERE stock_transfer_id = $1 ORDER BY line_number, id", transfer_id)
    if not items:
        raise _blok("PB_TANPA_BARANG", "Pemindahan tak punya baris barang.", 422)
    # SATU penentu yang sama dengan pratinjau; kunci=True mengunci baris stok FOR UPDATE. Blok pertama = galat tulis.
    rencana = await _rencana(conn, tid, h["from_warehouse_id"], h["to_warehouse_id"],
                             [{"item_id": i["item_id"], "qty": Decimal(str(i["quantity_requested"])), "notes": i["notes"]} for i in items],
                             h["sales_order_id"], kunci=True)
    angkat_blok_pertama(rencana)
    by_item = {i["item_id"]: i for i in items}
    asal, tujuan = rencana["dari"], rencana["ke"]
    hari = await tanggal_dokumen(conn, tid)
    total = _NOL
    for ln in rencana["lines"]:
        it = by_item[ln["item_id"]]
        qty, wac, nilai = ln["quantity"], ln["unit_cost"], ln["value"]
        saldo = await _saldo_produk(conn, tid, ln["item_id"])
        kw = dict(product_id=ln["item_id"], kode=ln["item_code"], nama=ln["name"], sumber=SUMBER, tanggal=hari,
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


async def buat_dan_selesaikan(conn, ctx: dict, body: PemindahanIn) -> dict:
    """ATOMIK (simpan = selesai, pola Accurate): draf + selesai dalam SATU transaksi pemanggil. Galat apa pun membatalkan
    SEMUANYA (tak ada draf yatim; nomor urut ikut rollback). -> {'id', 'ulang', 'catatan_fase'}."""
    tid_pb = await buat_draf(conn, ctx, body)
    hasil = await selesaikan(conn, ctx, tid_pb)
    return {"id": tid_pb, **hasil}


async def void(conn, ctx: dict, transfer_id, alasan: str) -> dict:
    """completed -> voided: pembalik ledger kedua kaki. Idempoten untuk yang sudah voided."""
    tid, uid = ctx["tenant_id"], ctx.get("user_id")
    h = await _draf_terkunci(conn, tid, transfer_id)
    if h["status"] == "voided":
        return {"ulang": True, "catatan_fase": None}
    if h["status"] != "completed":
        raise _blok("PB_STATUS_TIDAK_BOLEH", f"Pemindahan {h['transfer_number']} berstatus {h['status']}; hanya yang selesai bisa di-void.")
    asli_awal = await conn.fetch(
        "SELECT product_id, warehouse_id FROM inventory_ledger WHERE tenant_id = $1 AND source_type = $2 AND source_id = $3",
        tid, SUMBER, transfer_id)
    for p, w in sorted({(str(r["product_id"]), str(r["warehouse_id"])) for r in asli_awal}):
        await _kunci(conn, f"STOCK:{p}:{w}")
    rencana = await _rencana_void(conn, tid, h, kunci=True)
    angkat_blok_pertama(rencana)
    asli = rencana["asli"]
    hari = await tanggal_dokumen(conn, tid)
    # Kaki masuk asli (gudang tujuan) dibalik jadi KELUAR lebih dulu.
    urut = sorted(asli, key=lambda r: 0 if Decimal(str(r["quantity_in"])) > _NOL else 1)
    for r in urut:
        masuk_asli = Decimal(str(r["quantity_in"])) > _NOL
        qty = Decimal(str(r["quantity_in"] if masuk_asli else r["quantity_out"]))
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
