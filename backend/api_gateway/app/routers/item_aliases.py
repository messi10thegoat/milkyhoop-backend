"""Alias item per tenant (W3/U4 Conversational Workspace, Q-009, 25 Sep 2026).

GET /api/items/aliases -> {"aliases": [{"teks", "item_id"}]} seluruh alias tenant (urut teks);
alias yang itemnya dihapus-lunak (products.deleted_at) TIDAK dikirim.
PUT /api/items/aliases {"aliases": [...1-50 pasangan]} -> upsert per (tenant_id, teks),
SEMUA-ATAU-TIDAK: satu pasangan salah -> 400 {detail: {code, message, index}}, nol tersimpan.

Normalisasi teks = otoritas server: lower + trim + rapatkan spasi; "|" (pemisah varian) dipertahankan.
Item asing (tenant lain) dan item terhapus-lunak mendapat pesan yang SAMA -> tak membocorkan
keberadaan item tenant lain.

Router ini WAJIB di-include SEBELUM items.router (main.py): kalau tidak, "aliases" ditangkap
GET/PUT /items/{item_id}. Izin: permission_middleware (GET item R, PUT sales_order C) — entri
eksplisit di atas pola /api/items/[^/]+.
"""
import logging
from typing import Any, Optional
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request

logger = logging.getLogger(__name__)
router = APIRouter()

MAKS_PASANGAN = 50
MAKS_TEKS = 120


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


def normalisasi_teks(teks: str) -> str:
    """lower + trim + spasi tunggal (semua whitespace, termasuk tab/baris baru)."""
    return " ".join(teks.lower().split())


def _tolak(code: str, message: str, index: Optional[int] = None):
    detail = {"code": code, "message": message}
    if index is not None:
        detail["index"] = index
    raise HTTPException(status_code=400, detail=detail)


def _uuid_atau_none(nilai: Any) -> Optional[UUID]:
    try:
        return UUID(str(nilai))
    except (ValueError, TypeError, AttributeError):
        return None


def validasi_pasangan(badan: Any) -> list:
    """Badan PUT -> [(teks_normal, item_uuid)] atau 400. Tanpa DB (pagar tenant di handler)."""
    if not isinstance(badan, dict) or not isinstance(badan.get("aliases"), list):
        _tolak("ALIAS_BADAN_TIDAK_SAH", "Badan harus {\"aliases\": [...]}.")
    daftar = badan["aliases"]
    if not 1 <= len(daftar) <= MAKS_PASANGAN:
        _tolak("ALIAS_JUMLAH", f"Kirim 1 sampai {MAKS_PASANGAN} alias sekaligus.")
    hasil, dilihat = [], {}
    for i, p in enumerate(daftar):
        if not isinstance(p, dict) or not isinstance(p.get("teks"), str):
            _tolak("ALIAS_TEKS_TIDAK_SAH", "Teks alias wajib diisi.", i)
        teks = normalisasi_teks(p["teks"])
        if not teks:
            _tolak("ALIAS_TEKS_KOSONG", "Teks alias wajib diisi.", i)
        if len(teks) > MAKS_TEKS:
            _tolak("ALIAS_TEKS_PANJANG", f"Teks alias maksimal {MAKS_TEKS} karakter.", i)
        item = _uuid_atau_none(p.get("item_id"))
        if item is None:
            _tolak("ALIAS_ITEM_TIDAK_SAH", "Barang untuk alias ini tidak ditemukan.", i)
        if teks in dilihat:
            _tolak("ALIAS_TEKS_GANDA", f"Teks alias \"{teks}\" dikirim lebih dari sekali.", i)
        dilihat[teks] = i
        hasil.append((teks, item))
    return hasil


@router.get("/items/aliases")
async def daftar_alias(request: Request):
    ctx = get_user_context(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT a.teks, a.item_id
               FROM item_aliases a
               JOIN products p ON p.id = a.item_id AND p.tenant_id = a.tenant_id
               WHERE a.tenant_id = $1 AND p.deleted_at IS NULL
               ORDER BY a.teks""",
            ctx["tenant_id"],
        )
    return {"aliases": [{"teks": r["teks"], "item_id": str(r["item_id"])} for r in rows]}


@router.put("/items/aliases")
async def simpan_alias(request: Request):
    ctx = get_user_context(request)
    try:
        badan = await request.json()
    except Exception:
        _tolak("ALIAS_BADAN_TIDAK_SAH", "Badan harus {\"aliases\": [...]}.")
    pasangan = validasi_pasangan(badan)
    tenant_id = ctx["tenant_id"]
    item_ids = [it for _, it in pasangan]
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            # Pagar tenant: item_id wajib milik tenant ini DAN belum dihapus-lunak.
            milik = await conn.fetch(
                """SELECT id FROM products
                   WHERE tenant_id = $1 AND id = ANY($2::uuid[]) AND deleted_at IS NULL""",
                tenant_id, item_ids,
            )
            sah = {r["id"] for r in milik}
            for i, it in enumerate(item_ids):
                if it not in sah:
                    _tolak("ALIAS_ITEM_TIDAK_SAH", "Barang untuk alias ini tidak ditemukan.", i)
            rows = await conn.fetch(
                """INSERT INTO item_aliases (tenant_id, teks, item_id, created_by)
                   SELECT $1, t.teks, t.item_id, $4
                   FROM unnest($2::text[], $3::uuid[]) AS t(teks, item_id)
                   ON CONFLICT (tenant_id, teks)
                   DO UPDATE SET item_id = EXCLUDED.item_id, updated_at = now()
                   RETURNING teks, item_id""",
                tenant_id, [t for t, _ in pasangan], item_ids, _uuid_atau_none(ctx.get("user_id")),
            )
    tersimpan = {r["teks"]: str(r["item_id"]) for r in rows}
    return {"aliases": [{"teks": t, "item_id": tersimpan[t]} for t, _ in pasangan]}
