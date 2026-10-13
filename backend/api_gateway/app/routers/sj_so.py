"""Surat Jalan dari PESANAN tanpa faktur (R1 irisan 1 non-stok + irisan 2 ber-stok, 12 Okt 2026).

POST /api/sales-orders/{id}/delivery-notes/preview   -> pratinjau (satu penentu dgn tulis), 200 walau diblok, SEMUA blok
POST /api/sales-orders/{id}/delivery-notes           -> tulis ATOMIK (X-Idempotency-Key)
POST /api/deliveries/{id}/void/preview, /void        -> void MANDIRI (hanya asal 'so' yang belum ditagih)
GET  /api/delivery-notes/pending-invoice             -> laporan "terkirim belum difakturkan" + rekonsiliasi saldo Barang Terkirim
Di balik flag `pengiriman_sebelum_faktur` (non-stok) dan `pengiriman_stok_sebelum_faktur` (ber-stok); tanpa flag 409 SJ_SO_NONAKTIF.
Isi: services/sj_so.py."""
from decimal import Decimal
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field

from ..services import sj_so as S
from ..services import idem_buat
from ..services.role_resolver import AccountRole, resolve_account_id_by_role

router = APIRouter()
_ANGKA = ("ordered", "delivered_before", "quantity", "remaining_after", "unit_cost", "value", "available", "invoiced_quantity")


async def _pool():
    from ..services.db_pool import get_db_pool
    return await get_db_pool()


def _ctx(request: Request) -> dict:
    if not hasattr(request.state, "user"):
        raise HTTPException(status_code=401, detail="Authentication required")
    return {"tenant_id": request.state.user["tenant_id"], "user_id": request.state.user.get("user_id")}


def _f(x):
    return float(x) if isinstance(x, Decimal) else x


def _baris(l: dict) -> dict:
    return {k: (_f(v) if k in _ANGKA else v) for k, v in l.items()}


def _dilewati(daftar: list) -> list:
    """Entri dilewati -> JSON murni. SJ_SO_BARIS_SUDAH_DIFAKTUR membawa faktur[] {invoice_id, invoice_number} + invoice_id/number pertama."""
    return [dict(d) for d in daftar]


def _bentuk(r: dict) -> dict:
    so = r["so"]
    return {
        "ok": not r["blocks"],
        "blocks": [{"code": b["code"], "message": b["message"], "sales_order_item_id": b.get("sales_order_item_id"),
                    **({k: b[k] for k in ("faktur", "invoice_id", "invoice_number") if k in b})}
                   for b in r["blocks"]],
        "lines": [_baris(l) for l in r["lines"]],
        "dilewati": _dilewati(r["dilewati"]),
        "sales_order": {"id": str(so["id"]), "number": so["order_number"], "status": so["status"]},
        "fulfillment_date": r["fulfillment_date"].isoformat(),
        "warehouse": r.get("warehouse"),
        "total_value": _f(r.get("total_value", Decimal("0"))),
        "notes": r["notes"],
        "serah": r["serah"] and {"metode": r["serah"]["metode"], "kirim_ke": r["serah"]["kirim_ke"],
                                  "resi": r["serah"]["resi"]},
        "serah_aktif": r["serah_aktif"],
        "payload": r["payload"],
    }


def _uuid(v: str) -> UUID:
    try:
        return UUID(str(v))
    except (ValueError, TypeError):
        raise HTTPException(status_code=404, detail="Dokumen tidak ditemukan.")


@router.post("/sales-orders/{order_id}/delivery-notes/preview")
async def preview_sj_so(request: Request, order_id: str, body: Optional[S.SJSOIn] = None):
    ctx = _ctx(request)
    so_id = _uuid(order_id)
    pool = await _pool()
    async with pool.acquire() as conn:
        await S.pastikan_aktif(conn, ctx["tenant_id"])
        r = await S.rencana(conn, ctx, so_id, body or S.SJSOIn(), kunci=False)
        return {"success": True, "data": _bentuk(r)}


@router.post("/sales-orders/{order_id}/delivery-notes")
async def create_sj_so(request: Request, response: Response, order_id: str, body: Optional[S.SJSOIn] = None):
    """Buat Surat Jalan dari pesanan TANPA faktur. X-Idempotency-Key (alias Idempotency-Key): kunci sama + isi sama =
    respons asli (X-Idempotent-Replay); isi beda = 409 IDEMPOTENCY_KEY_REUSED."""
    ctx = _ctx(request)
    so_id = _uuid(order_id)
    body = body or S.SJSOIn()
    kunci = idem_buat.kunci_dari(request)
    pool = await _pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"])
            await S.pastikan_aktif(conn, ctx["tenant_id"])
            kp, sidik, lama = await idem_buat.mulai(conn, ctx, kunci, f"SJ_SO:{so_id}", body, "invoice_fulfillments",
                                                    "delivery", response)
            if lama is not None:
                return lama
            h = await S.tulis(conn, ctx, so_id, body)
            r = h["rencana"]
            resp = {"success": True, "message": "Surat Jalan dibuat",
                    "data": {"id": str(h["fulfillment_id"]), "delivery_number": h["fulfillment_number"], "asal": "so",
                             "status": "posted", "sales_order_id": str(so_id),
                             "fulfillment_date": r["fulfillment_date"].isoformat(),
                             "warehouse": r.get("warehouse"), "total_value": _f(r.get("total_value", Decimal("0"))),
                             "journal_id": str(h["journal_id"]) if h.get("journal_id") else None,
                             "lines": [_baris(l) for l in r["lines"]], "dilewati": _dilewati(r["dilewati"])}}
            return await idem_buat.simpan(conn, ctx, kp, sidik, "SJ_SO", resp, h["fulfillment_id"])


class AlasanIn(BaseModel):
    reason: Optional[str] = Field(None, max_length=500)


@router.post("/deliveries/{delivery_id}/void/preview")
async def preview_void_sj(request: Request, delivery_id: str, body: Optional[AlasanIn] = None):
    ctx = _ctx(request)
    fid = _uuid(delivery_id)
    pool = await _pool()
    async with pool.acquire() as conn:
        await S.pastikan_aktif(conn, ctx["tenant_id"])
        r = await S.rencana_void(conn, ctx["tenant_id"], fid, body.reason if body else None)
        return {"success": True, "data": {
            "ok": not r["blocks"], "ulang": r["ulang"], "delivery_number": r["sj"]["fulfillment_number"],
            "blocks": [{"code": b["code"], "message": b["message"]} for b in r["blocks"]],
            "payload": {"delivery_id": str(fid), "reason": body.reason if body else None}}}


@router.post("/deliveries/{delivery_id}/void")
async def void_sj(request: Request, delivery_id: str, body: AlasanIn):
    ctx = _ctx(request)
    fid = _uuid(delivery_id)
    pool = await _pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"])
            await S.pastikan_aktif(conn, ctx["tenant_id"])
            h = await S.void(conn, ctx, fid, (body.reason or "").strip())
            return {"success": True, "ulang": h["ulang"], "message": "Surat Jalan dibatalkan"}


# ---------------------------------------------------------------------------
# Laporan "terkirim belum difakturkan" (Law 1/16: saldo buku diturunkan dari jurnal; baris = sub-ledger snapshot SJ)
# ---------------------------------------------------------------------------
SQL_PENDING = """
SELECT f.id AS delivery_id, f.fulfillment_number, f.fulfillment_date, so.id AS so_id, so.order_number, so.customer_name,
       ifi.id AS item_id, ifi.product_id, COALESCE(p.nama_produk, soi.description) AS name, soi.unit,
       ifi.quantity, COALESCE(a.q, 0) AS ditagih, ifi.quantity - COALESCE(a.q, 0) AS belum,
       ifi.unit_cost, ifi.total_cost - COALESCE(a.c, 0) AS nilai_belum
FROM invoice_fulfillments f
JOIN invoice_fulfillment_items ifi ON ifi.fulfillment_id = f.id
LEFT JOIN sales_orders so ON so.id = f.sales_order_id AND so.tenant_id = f.tenant_id
LEFT JOIN sales_order_items soi ON soi.id = ifi.sales_order_item_id
LEFT JOIN products p ON p.id = ifi.product_id AND p.tenant_id = f.tenant_id
LEFT JOIN LATERAL (SELECT SUM(x.quantity) AS q, SUM(x.total_cost) AS c FROM sj_faktur_alokasi x
                   WHERE x.fulfillment_item_id = ifi.id AND x.tenant_id = f.tenant_id AND x.voided_at IS NULL) a ON true
WHERE f.tenant_id = $1 AND f.asal = 'so' AND f.status <> 'voided' AND f.voided_at IS NULL
  AND ifi.quantity - COALESCE(a.q, 0) > 0
ORDER BY f.fulfillment_date, f.fulfillment_number, ifi.id
"""
SQL_SALDO_BUKU = """
SELECT COALESCE(SUM(jl.debit - jl.credit), 0) FROM journal_lines jl JOIN journal_entries je ON je.id = jl.journal_id
WHERE je.tenant_id = $1 AND je.status = 'POSTED' AND is_effective_journal(je.id) AND jl.account_id = $2
"""


@router.get("/delivery-notes/pending-invoice")
async def pending_invoice(request: Request):
    """Barang yang sudah dikirim (Surat Jalan dari pesanan) tetapi BELUM difakturkan: per baris SJ + total nilai (snapshot WAC saat
    kirim) dan saldo akun Barang Terkirim dari jurnal. Selisih != 0 = ada jurnal tanpa SJ / SJ tanpa jurnal (dijaga Check harian)."""
    ctx = _ctx(request)
    pool = await _pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(SQL_PENDING, ctx["tenant_id"])
        saldo = None
        try:
            akun = await resolve_account_id_by_role(conn, ctx["tenant_id"], AccountRole.GOODS_DELIVERED_NOT_INVOICED)
            saldo = Decimal(str(await conn.fetchval(SQL_SALDO_BUKU, ctx["tenant_id"], akun)))
        except Exception:   # tenant tanpa peran (belum V417): laporan tetap jalan, saldo null
            saldo = None
        hari = await conn.fetchval("SELECT tanggal_bisnis($1)", ctx["tenant_id"])
        items, total, total_qty = [], Decimal("0"), Decimal("0")
        for r in rows:
            nilai = Decimal(str(r["nilai_belum"]))
            total += nilai
            total_qty += Decimal(str(r["belum"]))
            umur = (hari - r["fulfillment_date"]).days if hari and r["fulfillment_date"] else None
            items.append({
                "delivery_id": str(r["delivery_id"]), "delivery_number": r["fulfillment_number"],
                "delivery_date": r["fulfillment_date"].isoformat() if r["fulfillment_date"] else None, "umur_hari": umur,
                "sales_order_id": str(r["so_id"]) if r["so_id"] else None, "sales_order_number": r["order_number"],
                "customer_name": r["customer_name"], "item_id": str(r["item_id"]),
                "product_id": str(r["product_id"]) if r["product_id"] else None, "name": r["name"], "unit": r["unit"],
                "quantity": _f(Decimal(str(r["quantity"]))), "invoiced_quantity": _f(Decimal(str(r["ditagih"]))),
                "pending_quantity": _f(Decimal(str(r["belum"]))), "unit_cost": _f(Decimal(str(r["unit_cost"] or 0))),
                "pending_value": _f(nilai)})
        return {"success": True, "data": {
            "items": items, "total_pending_quantity": _f(total_qty), "total_pending_value": _f(total),
            "ledger_balance": _f(saldo) if saldo is not None else None,
            "selisih": _f(saldo - total) if saldo is not None else None}}
