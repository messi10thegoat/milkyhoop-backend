"""Tautkan faktur ke pesanan (BUG-006, putusan MASTER/Anton 25 Sep 2026).

POST /api/sales-invoices/{invoice_id}/link-order
  {"sales_order_id": uuid, "lines": [{"invoice_item_id", "sales_order_item_id"}]?, "dry_run": bool?}

Untuk faktur yang dibuat TANPA tautan ke pesanan padahal barangnya dari pesanan itu (kasus nyata:
SO 001-09-26 grapgrap — draf faktur lama dihapus 15 Sep sebelum DELETE draf melepas SO, lalu faktur
baru 23 Sep dibuat lepas). Akibatnya pemeriksa anomali benar: SO "tercatat 63 difakturkan" tapi
faktur bertaut 0, dan "selesai tanpa faktur".

Efek (satu transaksi, NOL jurnal — hanya tautan):
  1. sales_invoices.sales_order_id + sales_invoice_items.sales_order_item_id diisi.
  2. quantity_invoiced SETIAP baris SO itu DIHITUNG ULANG = SUM qty baris faktur bertaut non-void
     (rumus yang sama dengan anomalies._check_so_invoiced_mismatch), BUKAN ditambah — sisa draf
     hantu ikut terkoreksi. Hasil > qty pesanan -> 400, nol tertulis.
  3. audit_logs SALES_INVOICE_LINKED_TO_ORDER (sebelum/sesudah per baris SO) — tampil di
     GET /api/sales-invoices/{id}/history.
Pagar: faktur & SO satu tenant + satu pelanggan (customer_id); faktur void / SO batal ditolak;
faktur yang sudah bertaut (header atau baris) ditolak 409; item baris wajib sama (pemetaan otomatis
per item_id, atau pemetaan eksplisit bila satu item muncul di >1 baris SO).
dry_run=true -> rencana yang sama, nol tulisan (untuk dialog konfirmasi FE).
Trigger Law 19 hanya membekukan kolom NOMINAL; kolom tautan boleh diisi pada faktur terposting.
"""
import json
import logging
from decimal import Decimal
from typing import Any, Optional
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request

logger = logging.getLogger(__name__)
router = APIRouter()


async def get_pool():
    """Get singleton connection pool (Law 32)."""
    from ..services.db_pool import get_db_pool

    return await get_db_pool()


def get_user_context(request: Request) -> dict:
    if not hasattr(request.state, "user") or not request.state.user:
        raise HTTPException(status_code=401, detail="Authentication required")
    user = request.state.user
    tenant_id = user.get("tenant_id")
    if not tenant_id:
        raise HTTPException(status_code=401, detail="Invalid user context")
    return {"tenant_id": tenant_id, "user_id": user.get("user_id")}


def _tolak(status: int, code: str, message: str):
    raise HTTPException(status_code=status, detail={"code": code, "message": message})


def _uuid(nilai: Any, code: str, message: str) -> UUID:
    try:
        return UUID(str(nilai))
    except (ValueError, TypeError, AttributeError):
        _tolak(400, code, message)


def baca_badan(badan: Any) -> tuple:
    """-> (sales_order_id, pemetaan|None, dry_run). Bentuk salah = 400."""
    if not isinstance(badan, dict):
        _tolak(400, "TAUTAN_BADAN_TIDAK_SAH", "Badan harus objek JSON.")
    so_id = _uuid(badan.get("sales_order_id"), "TAUTAN_SO_TIDAK_SAH", "Pesanan tidak ditemukan.")
    pemetaan = badan.get("lines")
    if pemetaan is not None:
        if not isinstance(pemetaan, list) or not pemetaan:
            _tolak(400, "TAUTAN_BADAN_TIDAK_SAH", "lines harus daftar berisi minimal satu pasangan.")
        hasil = []
        for p in pemetaan:
            if not isinstance(p, dict):
                _tolak(400, "TAUTAN_BADAN_TIDAK_SAH", "Setiap pasangan harus objek.")
            hasil.append((
                _uuid(p.get("invoice_item_id"), "TAUTAN_BARIS_TIDAK_SAH", "Baris faktur tidak ditemukan."),
                _uuid(p.get("sales_order_item_id"), "TAUTAN_BARIS_TIDAK_SAH", "Baris pesanan tidak ditemukan."),
            ))
        pemetaan = hasil
    return so_id, pemetaan, bool(badan.get("dry_run", False))


def rencanakan_tautan(inv: dict, inv_items: list, so: dict, so_items: list,
                      pemetaan: Optional[list]) -> list:
    """Pagar + pasangan (invoice_item_id, sales_order_item_id). Murni: tanpa DB.

    inv: {status, customer_id, sales_order_id}; inv_items: [{id, item_id, quantity, sales_order_item_id}]
    so: {status, customer_id}; so_items: [{id, item_id, quantity}]
    """
    if inv["status"] == "void":
        _tolak(400, "TAUTAN_FAKTUR_VOID", "Faktur yang dibatalkan tidak bisa ditautkan ke pesanan.")
    if so["status"] == "cancelled":
        _tolak(400, "TAUTAN_SO_BATAL", "Pesanan yang dibatalkan tidak bisa ditautkan.")
    if not inv["customer_id"] or not so["customer_id"] or str(inv["customer_id"]) != str(so["customer_id"]):
        _tolak(400, "TAUTAN_PELANGGAN_BEDA", "Pelanggan faktur dan pesanan berbeda.")
    if inv["sales_order_id"] is not None or any(it["sales_order_item_id"] for it in inv_items):
        _tolak(409, "TAUTAN_SUDAH_ADA", "Faktur ini sudah bertaut ke pesanan.")

    per_inv = {it["id"]: it for it in inv_items}
    per_soi = {s["id"]: s for s in so_items}
    pasangan = []
    if pemetaan is not None:
        dipakai = set()
        for inv_item_id, soi_id in pemetaan:
            it, s = per_inv.get(inv_item_id), per_soi.get(soi_id)
            if it is None:
                _tolak(400, "TAUTAN_BARIS_TIDAK_SAH", "Baris faktur tidak ditemukan.")
            if s is None:
                _tolak(400, "TAUTAN_BARIS_TIDAK_SAH", "Baris pesanan tidak ditemukan.")
            if inv_item_id in dipakai:
                _tolak(400, "TAUTAN_BARIS_GANDA", "Satu baris faktur dipetakan lebih dari sekali.")
            dipakai.add(inv_item_id)
            if not it["item_id"] or it["item_id"] != s["item_id"]:
                _tolak(400, "TAUTAN_ITEM_BEDA", "Barang di baris faktur berbeda dengan baris pesanan.")
            pasangan.append((inv_item_id, soi_id))
    else:
        for it in inv_items:
            if not it["item_id"]:
                continue
            calon = [s for s in so_items if s["item_id"] == it["item_id"]]
            if len(calon) > 1:
                _tolak(400, "TAUTAN_PERLU_PEMETAAN",
                       "Satu barang muncul di beberapa baris pesanan; pilih pasangan barisnya.")
            if calon:
                pasangan.append((it["id"], calon[0]["id"]))
    if not pasangan:
        _tolak(400, "TAUTAN_TAK_ADA_BARIS_COCOK", "Tidak ada barang faktur yang ada di pesanan ini.")
    return pasangan


def hitung_ulang(so_items: list, bertaut_lain: dict, inv_items: list, pasangan: list) -> list:
    """quantity_invoiced BARU per baris SO = qty faktur bertaut non-void lain + qty faktur ini.
    bertaut_lain: {soi_id: Decimal} (sudah tanpa faktur ini). -> [{..., before, after}]; > qty -> 400."""
    qty_inv = {it["id"]: Decimal(str(it["quantity"])) for it in inv_items}
    tambah = {}
    for inv_item_id, soi_id in pasangan:
        tambah[soi_id] = tambah.get(soi_id, Decimal("0")) + qty_inv[inv_item_id]
    hasil = []
    for s in so_items:
        after = bertaut_lain.get(s["id"], Decimal("0")) + tambah.get(s["id"], Decimal("0"))
        if after > Decimal(str(s["quantity"])):
            _tolak(400, "TAUTAN_MELEBIHI_PESANAN",
                   f"Jumlah difakturkan untuk \"{s.get('description') or 'baris pesanan'}\" "
                   f"({after.normalize():f}) melebihi pesanan ({Decimal(str(s['quantity'])).normalize():f}).")
        hasil.append({"sales_order_item_id": s["id"], "description": s.get("description"),
                      "quantity": Decimal(str(s["quantity"])),
                      "before": Decimal(str(s["quantity_invoiced"])), "after": after})
    return hasil


def _f(v: Decimal) -> float:
    return float(v)


@router.post("/{invoice_id}/link-order")
async def tautkan_ke_pesanan(invoice_id: str, request: Request):
    ctx = get_user_context(request)
    tenant_id = ctx["tenant_id"]
    inv_id = _uuid(invoice_id, "TAUTAN_FAKTUR_TIDAK_SAH", "Faktur tidak ditemukan.")
    try:
        badan = await request.json()
    except Exception:
        _tolak(400, "TAUTAN_BADAN_TIDAK_SAH", "Badan harus objek JSON.")
    so_id, pemetaan, dry_run = baca_badan(badan)

    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", f"INVOICE:{inv_id}")
            inv = await conn.fetchrow(
                """SELECT id, invoice_number, status, customer_id, sales_order_id
                   FROM sales_invoices WHERE id = $1 AND tenant_id = $2 FOR UPDATE""",
                inv_id, tenant_id,
            )
            if not inv:
                _tolak(404, "TAUTAN_FAKTUR_TIDAK_ADA", "Faktur tidak ditemukan.")
            so = await conn.fetchrow(
                """SELECT id, order_number, status, customer_id
                   FROM sales_orders WHERE id = $1 AND tenant_id = $2""",
                so_id, tenant_id,
            )
            if not so:
                _tolak(404, "TAUTAN_SO_TIDAK_ADA", "Pesanan tidak ditemukan.")
            # Kunci baris SO: jalur lain yang mengubah quantity_invoiced (buat/hapus/void faktur) menunggu.
            so_items = [dict(r) for r in await conn.fetch(
                """SELECT id, item_id, description, quantity, quantity_invoiced
                   FROM sales_order_items WHERE sales_order_id = $1
                   ORDER BY sort_order, id FOR UPDATE""",
                so_id,
            )]
            inv_items = [dict(r) for r in await conn.fetch(
                """SELECT id, item_id, description, quantity, sales_order_item_id
                   FROM sales_invoice_items WHERE invoice_id = $1 ORDER BY line_number, id""",
                inv_id,
            )]
            pasangan = rencanakan_tautan(dict(inv), inv_items, dict(so), so_items, pemetaan)
            lain = await conn.fetch(
                """SELECT sii.sales_order_item_id AS soi, SUM(sii.quantity) AS q
                   FROM sales_invoice_items sii
                   JOIN sales_invoices si ON si.id = sii.invoice_id
                   WHERE si.tenant_id = $1 AND si.status <> 'void' AND si.id <> $3
                     AND sii.sales_order_item_id = ANY($2::uuid[])
                   GROUP BY 1""",
                tenant_id, [s["id"] for s in so_items], inv_id,
            )
            baris_so = hitung_ulang(so_items, {r["soi"]: Decimal(str(r["q"])) for r in lain},
                                    inv_items, pasangan)
            desk_inv = {it["id"]: it for it in inv_items}
            hasil = {
                "dry_run": dry_run,
                "invoice_id": str(inv_id),
                "invoice_number": inv["invoice_number"],
                "sales_order_id": str(so_id),
                "order_number": so["order_number"],
                "lines": [{"invoice_item_id": str(a), "sales_order_item_id": str(b),
                           "description": desk_inv[a]["description"],
                           "quantity": _f(Decimal(str(desk_inv[a]["quantity"])))} for a, b in pasangan],
                "so_lines": [{"sales_order_item_id": str(b["sales_order_item_id"]),
                              "description": b["description"], "quantity": _f(b["quantity"]),
                              "quantity_invoiced_before": _f(b["before"]),
                              "quantity_invoiced_after": _f(b["after"])} for b in baris_so],
            }
            if dry_run:
                return hasil

            await conn.execute(
                "UPDATE sales_invoices SET sales_order_id = $1 WHERE id = $2 AND tenant_id = $3",
                so_id, inv_id, tenant_id,
            )
            for a, b in pasangan:
                await conn.execute(
                    "UPDATE sales_invoice_items SET sales_order_item_id = $1 WHERE id = $2 AND invoice_id = $3",
                    b, a, inv_id,
                )
            for b in baris_so:
                if b["after"] != b["before"]:
                    await conn.execute(
                        "UPDATE sales_order_items SET quantity_invoiced = $1 WHERE id = $2 AND sales_order_id = $3",
                        b["after"], b["sales_order_item_id"], so_id,
                    )
            meta = {
                "entity_type": "SALES_INVOICE", "entity_id": str(inv_id),
                "action": "LINKED_TO_ORDER", "user_id": str(ctx.get("user_id") or ""),
                "sales_order_id": str(so_id), "order_number": so["order_number"],
                "changes": {"lines": hasil["lines"], "so_lines": hasil["so_lines"]},
            }
            await conn.execute(
                """INSERT INTO audit_logs (id, "userId", "eventType", entity_type, entity_id, entity_number,
                                          tenant_id, source, metadata, success, "createdAt")
                   VALUES (gen_random_uuid()::text, $1, 'SALES_INVOICE_LINKED_TO_ORDER', 'sales_invoices',
                           $2, $3, $4, 'api:sales_invoices.link_order', $5::jsonb, true, now())""",
                str(ctx["user_id"]) if ctx.get("user_id") else None, inv_id,
                inv["invoice_number"], tenant_id, json.dumps(meta),
            )
    logger.info("[LINK_ORDER] %s -> %s tenant=%s", inv["invoice_number"], so["order_number"], tenant_id)
    return hasil
