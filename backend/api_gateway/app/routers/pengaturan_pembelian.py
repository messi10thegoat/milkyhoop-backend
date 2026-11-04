"""Pengaturan Pembelian (V477 TERIMA-TABEL, 4 Nov 2026): toleransi lebih-terima per tenant. Pola kode_order (tabel per fitur).

GET /api/settings/purchase  baca (tenant_settings R)
PUT /api/settings/purchase  ubah (tenant_settings U + PEMILIK saja, dicek di sini dari DB) {penerimaan_toleransi_persen: 0..100}
Tanpa baris = 0 = perilaku lama (blok QTY_MELEBIHI_SISA). Penentu yang membacanya: po_rencana.toleransi_terima.
"""
from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, HTTPException, Request

from ..services import po_rencana as PR
from ..services.db_pool import get_db_pool

router = APIRouter()


def _ctx(request: Request) -> dict:
    u = getattr(request.state, "user", None) or {}
    if not u.get("tenant_id"):
        raise HTTPException(status_code=401, detail="Silakan masuk terlebih dahulu.")
    return {"tenant_id": u["tenant_id"], "user_id": u.get("user_id") or u.get("id")}


@router.get("/settings/purchase")
async def baca_pengaturan(request: Request):
    ctx = _ctx(request)
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        return {"success": True, "data": {"penerimaan_toleransi_persen": float(await PR.toleransi_terima(conn, ctx["tenant_id"]))}}


@router.put("/settings/purchase")
async def simpan_pengaturan(request: Request):
    ctx = _ctx(request)
    b = await request.json() or {}
    try:
        t = Decimal(str(b.get("penerimaan_toleransi_persen")))
    except (InvalidOperation, ValueError):
        t = None
    if t is None or not t.is_finite() or t < 0 or t > 100 or t != t.quantize(Decimal("0.01")):
        raise HTTPException(status_code=422, detail="Toleransi penerimaan wajib angka 0 sampai 100 (maksimal 2 desimal).")
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        from ..services.role_resolution import try_resolve_business_role
        if await try_resolve_business_role(conn, str(ctx["user_id"]), ctx["tenant_id"]) != "OWNER":
            raise HTTPException(status_code=403, detail="Hanya pemilik usaha yang dapat mengubah pengaturan pembelian.")
        await conn.execute(
            """INSERT INTO purchase_settings (tenant_id, penerimaan_toleransi_persen, updated_at, updated_by) VALUES ($1, $2, now(), $3)
               ON CONFLICT (tenant_id) DO UPDATE SET penerimaan_toleransi_persen = EXCLUDED.penerimaan_toleransi_persen,
                 updated_at = now(), updated_by = EXCLUDED.updated_by""", ctx["tenant_id"], t, ctx["user_id"])
    return {"success": True, "data": {"penerimaan_toleransi_persen": float(t)}}
