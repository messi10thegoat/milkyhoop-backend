"""Surat Jalan dari PESANAN tanpa faktur (R1 irisan 1, 12 Okt 2026) -- barang NON-STOK, nol jurnal/kartu stok.

POST /api/sales-orders/{id}/delivery-notes/preview   -> pratinjau (satu penentu dgn tulis), 200 walau diblok, SEMUA blok
POST /api/sales-orders/{id}/delivery-notes           -> tulis ATOMIK (X-Idempotency-Key)
POST /api/deliveries/{id}/void/preview, /void        -> void MANDIRI (hanya asal 'so' yang belum ditagih)
Di balik flag `pengiriman_sebelum_faktur` (kaos dulu); tanpa flag 409 SJ_SO_NONAKTIF. Isi: services/sj_so.py."""
from decimal import Decimal
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

from ..services import sj_so as S
from ..services import idem_buat

router = APIRouter()


async def _pool():
    from ..services.db_pool import get_db_pool
    return await get_db_pool()


def _ctx(request: Request) -> dict:
    if not hasattr(request.state, "user"):
        raise HTTPException(status_code=401, detail="Authentication required")
    return {"tenant_id": request.state.user["tenant_id"], "user_id": request.state.user.get("user_id")}


def _f(x):
    return float(x) if isinstance(x, Decimal) else x


def _bentuk(r: dict) -> dict:
    so = r["so"]
    return {
        "ok": not r["blocks"],
        "blocks": [{"code": b["code"], "message": b["message"], "sales_order_item_id": b.get("sales_order_item_id")}
                   for b in r["blocks"]],
        "lines": [{**l, **{k: _f(l[k]) for k in ("ordered", "delivered_before", "quantity", "remaining_after")}} for l in r["lines"]],
        "dilewati": r["dilewati"],
        "sales_order": {"id": str(so["id"]), "number": so["order_number"], "status": so["status"]},
        "fulfillment_date": r["fulfillment_date"].isoformat(),
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
                             "lines": [{**l, **{k: _f(l[k]) for k in ("ordered", "delivered_before", "quantity", "remaining_after")}}
                                       for l in r["lines"]],
                             "dilewati": r["dilewati"]}}
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
