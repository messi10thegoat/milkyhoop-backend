"""Pratinjau "Kirim barang" Faktur Penjualan (BUG-430/436, 28 Nov 2026; kontrak backend/docs/KONTRAK-fulfill-preview-faktur-20261128.md).

Inti = _execute_fulfillment YANG SAMA dengan POST /{id}/fulfill, dijalankan di savepoint; pemanggil WAJIB me-ROLLBACK transaksi luar
(nol tulis). Galat inti (HTTPException) -> blok berkode, HTTP tetap 200. Semua teks dari server; angka uang/HPP = hasil inti (journal-derived),
tak dihitung ulang di sini."""
from datetime import date
from decimal import Decimal
from typing import Callable, List, Optional
from uuid import UUID

from fastapi import HTTPException

from . import stok_baris as SB
from . import teks_galat as tg

KODE_DIKENAL = {"FULFILL_LINE_FULLY_CREDITED", "FULFILL_LINE_NOT_SHIPPABLE", "BATCH_STOK_KURANG", "STOK_KURANG", "SERIAL_WAJIB",
                "SERIAL_JUMLAH_TAK_SAMA", "SERIAL_TAK_TERSEDIA", "SATUAN_TAK_TERKONVERSI"}


def _pesan(e: HTTPException) -> str:
    d = e.detail
    return str(d.get("message") or d.get("detail") or d) if isinstance(d, dict) else str(d)


_TEKS_STATUS = {"pending": "Belum dikirim", "unfulfilled": "Belum dikirim", "partial": "Sebagian", "fulfilled": "Lunas terkirim",
                "not_applicable": "Tak perlu dikirim"}


def teks_status_kirim(st):
    return _TEKS_STATUS.get(st) if st else None   # BUG-443: teks Indonesia dari server (FE tak memetakan nilai mentah)


def kode_blok(e: HTTPException) -> str:
    """Kode berkode dari detail dict bila ada; selain itu dari status + kalimat (kosakata kontrak)."""
    d, m = e.detail, _pesan(e)
    if getattr(e, "code", None) == "GUDANG_TANPA_AKSES":  # PARITAS-4b 4b-3 (GalatBerkode dari pengaman tulis_kartu)
        return e.code
    if isinstance(d, dict) and d.get("code") in KODE_DIKENAL:
        return d["code"]
    if isinstance(d, dict) and d.get("code"):
        return str(d["code"])
    if "bukan milik faktur" in m:
        return "BARIS_BUKAN_MILIK_FAKTUR"
    if m.startswith("Sisa "):
        return "SISA_KURANG"
    if m.startswith("Gudang wajib"):
        return "GUDANG_WAJIB"
    if "Hanya faktur terbit" in m or "Status pengiriman" in m:
        return "FAKTUR_TAK_BISA_DIKIRIM"
    if "periode" in m.lower() and e.status_code == 403:
        return "PERIODE_TERTUTUP"
    if "batch" in m.lower() and ("stok" in m.lower() or "tersedia" in m.lower()):
        return "BATCH_STOK_KURANG"
    if "Stok" in m or "stok" in m:
        return "STOK_KURANG"
    return "KIRIM_DITOLAK"


def _blok(code: str, message: str, item_id=None) -> dict:
    b = {"code": code, "message": message}
    if item_id:
        b["invoice_item_id"] = str(item_id)
    return b


def _t(q, unit) -> str:
    return f"{tg.qty(q)} {unit}".strip() if unit else tg.qty(q)


def _ringkas(nama: str, q: Decimal, unit, ordered: Decimal, sisa: Decimal) -> str:
    akhir = "lunas terkirim" if sisa <= 0 else f"sisa {tg.qty(sisa)}"
    return f"{nama} — {_t(q, unit)} dari {tg.qty(ordered)} · {akhir}"


async def pratinjau_kirim_faktur(conn, ctx: dict, invoice_id: UUID, body: dict, execute: Callable, penjaga: Callable,
                                 cek_periode: Callable, kunci: Optional[Callable] = None) -> dict:
    tid = ctx["tenant_id"]
    body = body or {}
    blocks: List[dict] = []
    warnings: List[dict] = []
    req_items = body.get("items") or []
    if not req_items:
        blocks.append(_blok("ITEMS_KOSONG", "Pilih barang yang dikirim."))
    if kunci:
        await kunci(conn, tid, invoice_id)
    invoice = await conn.fetchrow(
        """SELECT id, invoice_number, customer_name, total_amount, invoice_date, status, fulfillment_status, warehouse_id
           FROM sales_invoices WHERE id=$1 AND tenant_id=$2""", invoice_id, tid)
    if not invoice:
        raise HTTPException(404, "Faktur tidak ditemukan.")
    try:
        gudang = UUID(str(body["warehouse_id"])) if body.get("warehouse_id") else None
        tgl = date.fromisoformat(str(body["fulfillment_date"])) if body.get("fulfillment_date") else None
        minta = [{"invoice_item_id": UUID(str(i["invoice_item_id"])), "quantity": Decimal(str(i["quantity"])),
                  "serials": i.get("serials"), "batch_id": UUID(str(i["batch_id"])) if i.get("batch_id") else None} for i in req_items]
    except (ValueError, KeyError, TypeError, ArithmeticError):
        raise HTTPException(422, "Isi pratinjau tidak sah (gudang/tanggal/baris/jumlah).")
    if tgl is None:
        from ..utils.tanggal_tenant import tanggal_dokumen
        tgl = await tanggal_dokumen(conn, tid)
    gnama = await SB.nama_gudang(conn, tid, gudang) if gudang else None
    if gudang:
        from . import akses_gudang as AG
        b = await AG.tolak(conn, tid, [gudang])  # PARITAS-4b 4b-3: pratinjau == tulis (tulis: pengaman tulis_kartu)
        if b:
            blocks.append(_blok(b["code"], b["message"]))
    try:
        penjaga(invoice)
        await cek_periode(conn, tid, tgl)
    except HTTPException as e:
        blocks.append(_blok("PERIODE_TERTUTUP" if e.status_code == 403 else "FAKTUR_TAK_BISA_DIKIRIM", _pesan(e)))

    # keadaan SEBELUM per baris
    ids = list({m["invoice_item_id"] for m in minta})
    awal = {}
    for r in await conn.fetch(
            """SELECT sii.id, sii.item_id, sii.description, sii.unit, sii.quantity, sii.fulfilled_qty FROM sales_invoice_items sii
               WHERE sii.invoice_id = $1 AND sii.id = ANY($2::uuid[])""", invoice_id, ids):
        awal[r["id"]] = r
    stok_awal = {}
    if gudang:
        for r in awal.values():
            if r["item_id"]:
                stok_awal[r["id"]] = await SB.tersedia_gudang(conn, tid, gudang, r["item_id"], "quantity")
    for m in minta:
        if m["invoice_item_id"] not in awal:
            blocks.append(_blok("BARIS_BUKAN_MILIK_FAKTUR", f"Baris {m['invoice_item_id']} bukan milik faktur {invoice['invoice_number']}.",
                                m["invoice_item_id"]))

    hasil = None
    batch_per_baris: dict = {}
    status_sesudah = None
    stok_akhir: dict = {}
    if not blocks:
        try:
            async with conn.transaction():  # savepoint: galat SQL inti tak meracuni transaksi luar
                hasil = await execute(conn, tid, ctx["user_id"], dict(invoice), minta, gudang, tgl,
                                      recognize_revenue=body.get("recognize_revenue", True), idempotency_key=None,
                                      notes=body.get("notes"), payload_hash=None)
                for r in await conn.fetch(
                        """SELECT fi.invoice_item_id, fi.batch_id, fi.quantity, b.batch_number, b.expiry_date
                           FROM invoice_fulfillment_items fi LEFT JOIN item_batches b ON b.id = fi.batch_id
                           WHERE fi.fulfillment_id = $1::uuid ORDER BY fi.id""", hasil["fulfillment_id"]):
                    batch_per_baris.setdefault(r["invoice_item_id"], []).append(r)
                if gudang:
                    for k, r in awal.items():
                        if r["item_id"]:
                            stok_akhir[k] = await SB.tersedia_gudang(conn, tid, gudang, r["item_id"], "quantity")
                status_sesudah = await conn.fetchval("SELECT fulfillment_status FROM sales_invoices WHERE id = $1", invoice_id)
        except HTTPException as e:
            blocks.append(_blok(kode_blok(e), _pesan(e)))
            hasil = None

    per_baris: dict = {}
    for m in minta:
        per_baris[m["invoice_item_id"]] = per_baris.get(m["invoice_item_id"], Decimal("0")) + m["quantity"]
    lines = []
    for k, q in per_baris.items():
        r = awal.get(k)
        if not r:
            continue
        nama, unit = r["description"] or "", r["unit"]
        ordered, kirim_lalu = Decimal(str(r["quantity"])), Decimal(str(r["fulfilled_qty"] or 0))
        sebelum, sesudah = ordered - kirim_lalu, ordered - kirim_lalu - q
        ringkas = _ringkas(nama, q, unit, ordered, sesudah)
        lines.append({
            "invoice_item_id": str(k), "description": nama, "item_name": nama, "unit": unit,
            "quantity": str(q), "quantity_text": _t(q, unit),
            "ordered": str(ordered), "shipped_before": str(kirim_lalu), "remaining_before": str(sebelum), "remaining_after": str(sesudah),
            "ordered_text": _t(ordered, unit), "shipped_before_text": _t(kirim_lalu, unit),
            "remaining_before_text": _t(sebelum, unit), "remaining_after_text": _t(sesudah, unit),
            "summary_text": ringkas, "detail_text": ringkas,
            "stock_before_text": tg.qty(stok_awal[k]) if k in stok_awal else None,
            "stock_after_text": tg.qty(stok_akhir[k]) if k in stok_akhir else None,
            "batches": [{"batch_id": str(b["batch_id"]) if b["batch_id"] else None, "batch_no": b["batch_number"],
                         "expiry_text": b["expiry_date"].isoformat() if b["expiry_date"] else None,
                         "quantity": str(b["quantity"]), "quantity_text": _t(b["quantity"], unit)} for b in batch_per_baris.get(k, [])
                        if b["batch_id"]],
            "serials": [s for m in minta if m["invoice_item_id"] == k for s in (m.get("serials") or [])],
        })
        if sesudah > 0 and hasil is not None:
            warnings.append({"code": "MASIH_SISA", "message": f"Sisa {nama} {_t(sesudah, unit)} belum dikirim."})
    ok = not blocks and hasil is not None
    if not ok:   # BUG-443: diblok = tak ada yang berubah -> nilai 'sesudah'/rincian hasil dikosongkan (medan mentah SEBELUM tetap)
        for ln in lines:
            for f in ("remaining_after", "remaining_after_text", "summary_text", "detail_text", "stock_after_text", "batches", "serials", "cogs_text"):
                ln[f] = None
    totals = {"cogs": hasil["total_cogs"], "cogs_text": tg.rp(hasil["total_cogs"]),
              "revenue": hasil["total_revenue"], "revenue_text": tg.rp(hasil["total_revenue"])} if ok else None
    return {
        "can_fulfill": ok, "ok": ok, "blocks": blocks, "warnings": warnings,
        "invoice": {"id": str(invoice["id"]), "number": invoice["invoice_number"], "customer_name": invoice["customer_name"],
                    "fulfillment_status_before": invoice["fulfillment_status"],
                    "fulfillment_status_before_text": teks_status_kirim(invoice["fulfillment_status"]),
                    "fulfillment_status_after": status_sesudah if ok else None,
                    "fulfillment_status_after_text": teks_status_kirim(status_sesudah) if ok else None},
        "warehouse": {"id": str(gudang), "name": gnama} if gudang else None,
        "fulfillment_date": tgl.isoformat(), "lines": lines, "totals": totals,
        "journal_text": (f"HPP {totals['cogs_text']} dijurnal Dr HPP / Cr Persediaan; pendapatan {totals['revenue_text']} diakui.")
        if ok else None,
    }
