"""Fase LOKASI pesanan (7 Okt 2026, MASTER GO opsi A): "Dikirim ke {gudang}" / "Tersedia di {gudang}".

POST /api/sales-orders/{order_id}/fase-lokasi/preview  body {fase, warehouse_id} -> boleh/blok (NOL tulis), penentu = services/so_fase_lokasi.rencana
POST /api/sales-orders/{order_id}/fase-lokasi          body {fase, warehouse_id} + X-Idempotency-Key (opsional, pola W0) -> satu transaksi:
                                                       update + riwayat (audit_logs). fase=null & warehouse_id=null = HAPUS fase.
Izin = sales_order U (ubah pesanan). Data operasional: nol jurnal/stok/HPP (lihat docstring service)."""
import logging
import uuid
from typing import Literal, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi import Response as _Response
from pydantic import BaseModel

from ..services import idem_buat
from ..services import so_fase_lokasi as SF

logger = logging.getLogger(__name__)

router = APIRouter()


async def get_pool():
    from ..services.db_pool import get_db_pool
    return await get_db_pool()


def _ctx(request: Request) -> dict:
    user = getattr(request.state, "user", None)
    if not user or not user.get("tenant_id"):
        raise HTTPException(status_code=401, detail="Authentication required")
    return {"tenant_id": user["tenant_id"], "user_id": user.get("user_id") or user.get("id")}


def _uuid_or_404(v: str):
    try:
        return uuid.UUID(str(v))
    except ValueError:
        raise HTTPException(status_code=404, detail="Pesanan penjualan tidak ditemukan.")


class FaseLokasiRequest(BaseModel):
    fase: Optional[Literal["dikirim_ke_lokasi", "tersedia"]] = None
    warehouse_id: Optional[uuid.UUID] = None


@router.post("/{order_id}/fase-lokasi/preview")
async def fase_lokasi_preview(request: Request, order_id: str, body: FaseLokasiRequest):
    """Pratinjau (NOL tulis): {can, blocks[{code,message}], berubah, lama, baru, position_text}."""
    try:
        ctx = _ctx(request)
        so_id = _uuid_or_404(order_id)
        pool = await get_pool()
        async with pool.acquire() as conn:
            r = await SF.rencana(conn, ctx, so_id, body.fase, body.warehouse_id)
            g = r["gudang"]
            return {"success": True, "data": {
                "can": not r["blocks"],
                "blocks": [{"code": b["code"], "message": b["message"]} for b in r["blocks"]],
                "berubah": r["berubah"], "lama": r["lama"], "baru": r["baru"],
                "position_text": SF.teks_fase(body.fase, g["name"] if g else None),
            }}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error preview fase lokasi {order_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Gagal memeriksa fase lokasi pesanan")


@router.post("/{order_id}/fase-lokasi")
async def fase_lokasi_ubah(request: Request, order_id: str, body: FaseLokasiRequest, response: _Response = None):
    """Ubah/hapus fase lokasi. Penentu SAMA dengan pratinjau (blok pertama = galat, kode sama)."""
    try:
        ctx = _ctx(request)
        so_id = _uuid_or_404(order_id)
        kunci = idem_buat.kunci_dari(request)
        pool = await get_pool()
        async with pool.acquire() as conn:
            async with conn.transaction():
                kp, sd, lama = await idem_buat.mulai_aksi(conn, ctx, kunci, "SO_FASE", so_id, body.model_dump(mode="json"), response)
                if lama is not None:
                    return lama
                data = await SF.terapkan(conn, ctx, so_id, body.fase, body.warehouse_id)
                return await idem_buat.simpan(conn, ctx, kp, sd, "SO_FASE_LOKASI", {"success": True, "data": data}, so_id)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error ubah fase lokasi {order_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Gagal mengubah fase lokasi pesanan")
