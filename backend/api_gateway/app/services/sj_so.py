"""Surat Jalan dari PESANAN tanpa faktur -- barang NON-STOK (R1 IRISAN 1, 12 Okt 2026, putusan MASTER).

Pola rujukan: NetSuite Item Fulfillment dari Sales Order / SAP Outbound Delivery / Accurate Pengiriman Pesanan -- dokumen kirim
berdiri sendiri; faktur menyusul (atau sudah ada, jalur lama tetap).

TANPA aturan buku baru: barang non-stok => SJ = DOKUMEN. Nol jurnal, nol kartu stok (`inventory_ledger`), nol HPP, nol
`warehouse_stock`. Pendapatan non-stok tetap diakui saat faktur DIPOSTING (jalur lama). Barang ber-stok -> 409
SJ_SO_STOK_BELUM_DIDUKUNG sampai irisan 2 (akun Barang Terkirim, butuh P1+P2 pemilik).

Satu penentu `rencana` untuk pratinjau DAN tulis (kunci=True mengunci baris SO FOR UPDATE; blok pertama = galat tulis).
Satu transaksi milik PEMANGGIL (Law 23); kunci `SJ_SO:{tenant}:{so}` + kunci INVOICE_FULFILL tiap faktur SO (urut, kunci yang sama
dengan jalur faktur: tak ada kirim ganda lintas jalur) -> baca sisa -> tulis -> audit (Law 12). Tenant eksplisit di tiap SQL (Law 24).

Tautan ke faktur = `sj_faktur_alokasi` (INSERT-only; hanya voided_*). SJ-so TIDAK PERNAH di-UPDATE untuk menaut faktur
(UPDATE memicu trg_so_shipped_recompute); alokasi dibuat saat faktur diposting (`alokasikan_saat_faktur`).
"""
from datetime import date
from decimal import Decimal
from typing import List, Optional
from uuid import UUID, uuid4

from fastapi import HTTPException
from pydantic import BaseModel, Field

from . import pengiriman_serah as serah_mod
from . import so_kirim
from .so_riwayat import catat_riwayat
from ..utils.tanggal_tenant import tanggal_dokumen

FLAG = "pengiriman_sebelum_faktur"
_NOL = Decimal("0")
STATUS_SO_TERTUTUP = ("draft", "cancelled", "completed")


class BarisIn(BaseModel):
    sales_order_item_id: UUID
    quantity: Decimal = Field(..., gt=0)


class SJSOIn(BaseModel):
    items: Optional[List[BarisIn]] = None          # absen/null -> semua sisa baris non-stok yang perlu dikirim
    fulfillment_date: Optional[date] = None        # kosong -> tanggal bisnis tenant
    notes: Optional[str] = Field(None, max_length=1000)
    serah: Optional[serah_mod.SerahIn] = None


def _blok(kode: str, pesan: str, status: int = 409, baris=None) -> dict:
    return {"code": kode, "message": pesan, "status": status, "sales_order_item_id": str(baris) if baris else None}


def angkat_blok_pertama(r: dict) -> None:
    if r["blocks"]:
        b = r["blocks"][0]
        raise HTTPException(status_code=b["status"], detail={"code": b["code"], "message": b["message"]})


async def aktif(conn, tenant_id: str) -> bool:
    return bool(await conn.fetchval(
        "SELECT 1 FROM tenant_features WHERE tenant_id = $1 AND feature = $2 AND enabled", tenant_id, FLAG))


async def pastikan_aktif(conn, tenant_id: str) -> None:
    if not await aktif(conn, tenant_id):
        raise HTTPException(status_code=409, detail={
            "code": "SJ_SO_NONAKTIF", "message": "Pengiriman sebelum faktur belum tersedia untuk usaha ini."})


async def _kunci(conn, kunci: str) -> None:
    await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", kunci)


async def kunci_so(conn, tenant_id: str, so_id) -> None:
    """Kunci dokumen + kunci faktur (INVOICE_FULFILL, urut id) -- sama dengan so_pengiriman.kunci_faktur_so."""
    await _kunci(conn, f"SJ_SO:{tenant_id}:{so_id}")
    for f in await conn.fetch(
            "SELECT id FROM sales_invoices WHERE tenant_id = $1 AND sales_order_id = $2 AND status <> 'void' ORDER BY id",
            tenant_id, so_id):
        await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1), hashtext($2))", tenant_id, f"INVOICE_FULFILL:{f['id']}")


async def rencana(conn, ctx: dict, so_id, body: SJSOIn, kunci: bool = False) -> dict:
    """-> {so, blocks[{code,message,status,sales_order_item_id}], lines[...], dilewati[...], fulfillment_date, serah, serah_aktif, payload}.
    Tanpa tulis."""
    tid = ctx["tenant_id"]
    blocks: list = []
    so = await conn.fetchrow(
        "SELECT id, order_number, status, customer_id, customer_name FROM sales_orders WHERE id = $1 AND tenant_id = $2"
        + (" FOR UPDATE" if kunci else ""), so_id, tid)
    if not so:
        raise HTTPException(status_code=404, detail="Pesanan penjualan tidak ditemukan.")
    if so["status"] in STATUS_SO_TERTUTUP:
        label = {"draft": "masih draf", "cancelled": "sudah dibatalkan", "completed": "sudah selesai"}[so["status"]]
        blocks.append(_blok("SJ_SO_STATUS_TIDAK_BOLEH", f"Pesanan {so['order_number']} {label}; tak bisa dibuatkan Surat Jalan."))

    baris = await conn.fetch(
        """SELECT soi.id, soi.item_id, soi.description, soi.quantity, soi.unit, soi.perlu_kirim,
                  COALESCE(p.track_inventory, false) AS stok, p.nama_produk
           FROM sales_order_items soi
           LEFT JOIN products p ON p.id = soi.item_id AND p.tenant_id = $2
           WHERE soi.sales_order_id = $1 ORDER BY soi.sort_order NULLS LAST, soi.id""", so_id, tid)
    peta = {b["id"]: b for b in baris}
    terkirim, _ = await so_kirim.terkirim_per_baris(conn, tid, [so_id])
    dilewati: list = []
    diminta: dict = {}
    if body.items is None:
        for b in baris:
            perlu = b["perlu_kirim"] if b["perlu_kirim"] is not None else b["stok"]
            sisa = Decimal(str(b["quantity"])) - terkirim.get(b["id"], _NOL)
            if not perlu or sisa <= _NOL:
                continue
            if b["stok"]:
                dilewati.append({"sales_order_item_id": str(b["id"]), "name": b["nama_produk"] or b["description"],
                                 "alasan": "SJ_SO_STOK_BELUM_DIDUKUNG"})
                continue
            diminta[b["id"]] = sisa
    else:
        for it in body.items:
            b = peta.get(it.sales_order_item_id)
            if b is None:
                blocks.append(_blok("SJ_SO_BARIS_TAK_ADA", "Baris tidak ada di pesanan ini.", 404, it.sales_order_item_id))
                continue
            if it.sales_order_item_id in diminta:
                blocks.append(_blok("SJ_SO_BARIS_GANDA", "Baris yang sama muncul dua kali; gabungkan.", 422, b["id"]))
                continue
            nama = b["nama_produk"] or b["description"]
            perlu = b["perlu_kirim"] if b["perlu_kirim"] is not None else b["stok"]
            if b["stok"]:
                blocks.append(_blok("SJ_SO_STOK_BELUM_DIDUKUNG",
                                    f"{nama} ber-stok: Surat Jalan tanpa faktur untuk barang ber-stok belum didukung.", 409, b["id"]))
                continue
            if not perlu:
                blocks.append(_blok("SJ_SO_BARIS_TIDAK_PERLU_KIRIM", f"{nama} tidak perlu dikirim.", 409, b["id"]))
                continue
            sisa = Decimal(str(b["quantity"])) - terkirim.get(b["id"], _NOL)
            if it.quantity > sisa:
                blocks.append(_blok("SJ_SO_QTY_MELEBIHI_SISA",
                                    f"Sisa kirim {nama} hanya {sisa:f}, diminta {it.quantity:f}.", 409, b["id"]))
                continue
            diminta[b["id"]] = it.quantity

    lines = []
    for bid, qty in diminta.items():
        b = peta[bid]
        ada = terkirim.get(bid, _NOL)
        lines.append({"sales_order_item_id": str(bid), "item_id": str(b["item_id"]) if b["item_id"] else None,
                      "name": b["nama_produk"] or b["description"], "unit": b["unit"],
                      "ordered": Decimal(str(b["quantity"])), "delivered_before": ada, "quantity": qty,
                      "remaining_after": Decimal(str(b["quantity"])) - ada - qty})
    if not lines and not any(b["code"] != "SJ_SO_STATUS_TIDAK_BOLEH" for b in blocks):
        blocks.append(_blok("SJ_SO_TANPA_SISA", "Tidak ada barang non-stok yang tersisa untuk dikirim di pesanan ini."))

    flag = await serah_mod.aktif(conn, tid)
    serah = None
    if body.serah is not None and not flag:
        e = serah_mod.galat_nonaktif()
        blocks.append(_blok("SERAH_NONAKTIF", serah_mod.pesan_galat(e), 409))
    elif flag:
        try:
            serah = await serah_mod.selesaikan(conn, tid, so_id, body.serah)
        except HTTPException as e:
            blocks.append(_blok("SERAH_TIDAK_SAH", serah_mod.pesan_galat(e), 422))
    tgl = body.fulfillment_date or await tanggal_dokumen(conn, tid)
    return {"so": so, "blocks": blocks, "lines": lines, "dilewati": dilewati, "fulfillment_date": tgl, "serah": serah,
            "serah_aktif": flag, "notes": body.notes,
            "payload": {"items": [{"sales_order_item_id": l["sales_order_item_id"], "quantity": str(l["quantity"])} for l in lines],
                        "fulfillment_date": tgl.isoformat(), "notes": body.notes}}


async def tulis(conn, ctx: dict, so_id, body: SJSOIn) -> dict:
    """Satu SJ-so (header + baris) di transaksi PEMANGGIL. -> {fulfillment_id, fulfillment_number, rencana}."""
    tid, uid = ctx["tenant_id"], ctx.get("user_id")
    await kunci_so(conn, tid, so_id)
    r = await rencana(conn, ctx, so_id, body, kunci=True)
    angkat_blok_pertama(r)
    so = r["so"]
    nomor = await conn.fetchval("SELECT get_next_journal_number($1, $2, $3)", tid, "SJ", r["fulfillment_date"])
    fid = uuid4()
    await conn.execute(
        """INSERT INTO invoice_fulfillments (id, tenant_id, invoice_id, sales_order_id, asal, fulfillment_number, fulfillment_date,
                                            warehouse_id, status, notes, created_by, posted_at, posted_by, created_at, updated_at)
           VALUES ($1, $2, NULL, $3, 'so', $4, $5, NULL, 'posted', $6, $7, NOW(), $7, NOW(), NOW())""",
        fid, tid, so_id, nomor, r["fulfillment_date"], r["notes"], uid)
    for l in r["lines"]:
        await conn.execute(
            """INSERT INTO invoice_fulfillment_items (id, fulfillment_id, invoice_item_id, sales_order_item_id, product_id,
                                                      quantity, unit_cost, total_cost)
               VALUES ($1, $2, NULL, $3, $4, $5, 0, 0)""",
            uuid4(), fid, UUID(l["sales_order_item_id"]), UUID(l["item_id"]) if l["item_id"] else None, l["quantity"])
    if r.get("serah") is not None:   # SJ + serah + resi: SATU transaksi (Law 23); nol UPDATE ke SJ
        await serah_mod.tulis(conn, tid, uid, fid, r["serah"])
    await catat_riwayat(conn, tid, "invoice_fulfillments", fid, nomor, "SURAT_JALAN_SO_DIBUAT", uid,
                        f"Surat Jalan {nomor} dibuat dari pesanan {so['order_number']} (tanpa faktur, {len(r['lines'])} baris)",
                        {"sales_order_id": str(so_id), "baris": len(r["lines"])}, source="api:sj_so.create")
    return {"fulfillment_id": fid, "fulfillment_number": nomor, "rencana": r}


# ----------------------------------------------------------------------------
# VOID mandiri (hanya asal 'so' yang belum ditagih)
# ----------------------------------------------------------------------------
async def rencana_void(conn, tid: str, fid, alasan: Optional[str], kunci: bool = False) -> dict:
    blocks: list = []
    f = await conn.fetchrow(
        """SELECT f.id, f.fulfillment_number, f.status, f.asal, f.sales_order_id, f.voided_at
           FROM invoice_fulfillments f WHERE f.id = $1 AND f.tenant_id = $2""" + (" FOR UPDATE" if kunci else ""), fid, tid)
    if not f:
        raise HTTPException(status_code=404, detail="Surat Jalan tidak ditemukan.")
    ulang = f["status"] == "voided"
    if f["asal"] != "so":
        blocks.append(_blok("SJ_VOID_ASAL_FAKTUR",
                            "Surat Jalan ini milik faktur; pembatalannya lewat void faktur, bukan mandiri."))
    elif not ulang:
        akt = await conn.fetch(
            """SELECT si.invoice_number, SUM(a.quantity) AS q FROM sj_faktur_alokasi a
               JOIN invoice_fulfillment_items i ON i.id = a.fulfillment_item_id
               JOIN sales_invoice_items sii ON sii.id = a.invoice_item_id
               JOIN sales_invoices si ON si.id = sii.invoice_id AND si.tenant_id = a.tenant_id
               WHERE i.fulfillment_id = $1 AND a.tenant_id = $2 AND a.voided_at IS NULL GROUP BY si.invoice_number""", fid, tid)
        if akt:
            blocks.append(_blok("SJ_SUDAH_DITAGIH", "Surat Jalan ini sudah ditautkan ke faktur " +
                                ", ".join(sorted(a["invoice_number"] for a in akt)) + "; batalkan fakturnya dulu."))
    if not ulang and not (alasan or "").strip():
        blocks.append(_blok("SJ_ALASAN_WAJIB", "Alasan pembatalan wajib diisi.", 422))
    return {"sj": f, "blocks": blocks, "ulang": ulang}


async def void(conn, ctx: dict, fid, alasan: str) -> dict:
    tid, uid = ctx["tenant_id"], ctx.get("user_id")
    pre = await conn.fetchrow("SELECT sales_order_id FROM invoice_fulfillments WHERE id = $1 AND tenant_id = $2", fid, tid)
    if not pre:
        raise HTTPException(status_code=404, detail="Surat Jalan tidak ditemukan.")
    if pre["sales_order_id"] is not None:
        await kunci_so(conn, tid, pre["sales_order_id"])
    r = await rencana_void(conn, tid, fid, alasan, kunci=True)
    if r["ulang"] and r["sj"]["asal"] == "so":
        return {"ulang": True}
    angkat_blok_pertama(r)
    # trigger trg_so_shipped_recompute (AFTER UPDATE) menghitung ulang SO: yang DIHARAPKAN pada void.
    await conn.execute(
        "UPDATE invoice_fulfillments SET status = 'voided', voided_at = NOW(), voided_reason = $3, updated_at = NOW() "
        "WHERE id = $1 AND tenant_id = $2 AND asal = 'so'", fid, tid, alasan)
    await catat_riwayat(conn, tid, "invoice_fulfillments", fid, r["sj"]["fulfillment_number"], "SURAT_JALAN_SO_DIBATALKAN", uid,
                        f"Surat Jalan {r['sj']['fulfillment_number']} dibatalkan: {alasan}", {"alasan": alasan},
                        source="api:sj_so.void")
    return {"ulang": False}


# ----------------------------------------------------------------------------
# Alokasi saat faktur diposting (dipanggil sales_invoices di transaksi posting) & pelepasan saat void faktur
# ----------------------------------------------------------------------------
async def alokasikan_saat_faktur(conn, tenant_id: str, user_id, invoice_id) -> Decimal:
    """Tautkan sisa SJ-so (non-stok, belum teralokasi) ke baris faktur bertautan baris SO yang sama, TERTUA-DULU, lalu naikkan
    `sales_invoice_items.fulfilled_qty` sebesar alokasi (status pengiriman faktur dihitung blok V318 sesudahnya).
    Hanya baris `perlu_kirim` non-stok. INSERT-only ke sj_faktur_alokasi. Tanpa SJ-so -> nol efek. -> total qty dialokasikan."""
    if not await conn.fetchval(
            "SELECT 1 FROM invoice_fulfillments WHERE tenant_id = $1 AND asal = 'so' AND status <> 'voided' LIMIT 1", tenant_id):
        return _NOL
    items = await conn.fetch(
        """SELECT sii.id, sii.sales_order_item_id, sii.quantity, COALESCE(sii.fulfilled_qty, 0) AS fulfilled_qty
           FROM sales_invoice_items sii
           LEFT JOIN products p ON p.id = sii.item_id AND p.tenant_id = $2
           WHERE sii.invoice_id = $1 AND sii.sales_order_item_id IS NOT NULL AND COALESCE(sii.perlu_kirim, false)
             AND NOT COALESCE(p.track_inventory, false)
           ORDER BY sii.id""", invoice_id, tenant_id)
    total = _NOL
    for it in items:
        butuh = Decimal(str(it["quantity"])) - Decimal(str(it["fulfilled_qty"]))
        if butuh <= _NOL:
            continue
        sisa_sj = await conn.fetch(
            """SELECT i.id, i.quantity - COALESCE((SELECT SUM(a.quantity) FROM sj_faktur_alokasi a
                                                   WHERE a.fulfillment_item_id = i.id AND a.voided_at IS NULL), 0) AS sisa
               FROM invoice_fulfillment_items i
               JOIN invoice_fulfillments f ON f.id = i.fulfillment_id AND f.tenant_id = $1 AND f.asal = 'so'
                    AND f.status <> 'voided' AND f.voided_at IS NULL
               WHERE i.sales_order_item_id = $2
               ORDER BY f.fulfillment_date, f.created_at, i.id FOR UPDATE OF i""", tenant_id, it["sales_order_item_id"])
        dapat = _NOL
        for s in sisa_sj:
            sisa = Decimal(str(s["sisa"]))
            if sisa <= _NOL or butuh <= _NOL:
                continue
            ambil = sisa if sisa < butuh else butuh
            await conn.execute(
                """INSERT INTO sj_faktur_alokasi (tenant_id, fulfillment_item_id, invoice_item_id, quantity, created_by)
                   VALUES ($1, $2, $3, $4, $5)""", tenant_id, s["id"], it["id"], ambil, user_id)
            butuh -= ambil
            dapat += ambil
        if dapat > _NOL:
            await conn.execute(
                "UPDATE sales_invoice_items SET fulfilled_qty = COALESCE(fulfilled_qty, 0) + $2 WHERE id = $1", it["id"], dapat)
            total += dapat
    return total


async def lepas_alokasi_faktur(conn, tenant_id: str, invoice_id, alasan: str) -> int:
    """Void faktur: alokasi aktif ke baris faktur ini ditandai batal (SJ-so KEMBALI 'belum ditagih', TIDAK ikut void)."""
    r = await conn.execute(
        """UPDATE sj_faktur_alokasi a SET voided_at = NOW(), voided_reason = $3
           FROM sales_invoice_items sii
           WHERE a.invoice_item_id = sii.id AND sii.invoice_id = $1 AND a.tenant_id = $2 AND a.voided_at IS NULL""",
        invoice_id, tenant_id, alasan)
    try:
        return int(str(r).split()[-1])
    except (ValueError, IndexError):
        return 0
