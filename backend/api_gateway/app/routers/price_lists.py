"""
Price Lists Router - Daftar Harga Management
"""

from fastapi import APIRouter, HTTPException, Request, Query
from typing import Literal, Optional
from uuid import UUID
import logging
import asyncpg

from ..schemas.price_lists import (
    CreatePriceListRequest,
    UpdatePriceListRequest,
    PriceListItemCreate,
    PriceListItemUpdate,
    PriceListResponse,
    PriceListListResponse,
    PriceListDetailResponse,
    PriceListDropdownResponse,
    ResolveHargaRequest,
)
from ..services import harga_jual as HJ
from ..services import satuan_dasar as SD
from ..services.galat_berkode import GalatBerkode
from ..utils.tanggal_tenant import tanggal_dokumen

GAGAL = "Gagal memproses daftar harga."
TAK_ADA = "Daftar harga tidak ditemukan."


def _galat_unik(e) -> GalatBerkode:
    """Pelanggaran unik -> 409 berkalimat (dulu 500 "Failed")."""
    n = getattr(e, "constraint_name", "") or ""
    if "bawaan" in n:
        return GalatBerkode(409, "DAFTAR_BAWAAN_GANDA", "Usaha ini sudah punya daftar harga bawaan. Jadikan bawaan yang lama bukan bawaan dulu.")
    if "satuan" in n or "items" in n:
        return GalatBerkode(409, "HARGA_GANDA", "Harga untuk barang, satuan, dan jumlah minimum itu sudah ada di daftar ini.")
    return _kode_ganda()


def _kode_ganda() -> GalatBerkode:
    return GalatBerkode(409, "KODE_DAFTAR_GANDA", "Kode daftar harga itu sudah dipakai.", field="code")


def _satuan(unit):
    return (unit or None) and " ".join(unit.split())


async def siapkan_baris_harga(conn, tid: str, pl_id, item, line_id=None):
    """PENENTU BERSAMA penulis + pratinjau (BACA SAJA; GalatBerkode, kalimat == tulis): barang milik usaha, satuan dasar/berkonversi
    (penentu satuan_dasar), unik barang + satuan + qty minimum di daftar ini (baris yang diubah dikecualikan). -> id barang."""
    try:
        pid = UUID(str(item.item_id))
    except (ValueError, TypeError):
        raise GalatBerkode(400, "BARANG_TIDAK_DITEMUKAN", "Barang tidak ditemukan di usaha ini.", field="item_id")
    if not await conn.fetchval("SELECT 1 FROM products WHERE id = $1 AND tenant_id = $2", pid, tid):
        raise GalatBerkode(400, "BARANG_TIDAK_DITEMUKAN", "Barang tidak ditemukan di usaha ini.", field="item_id")
    if SD.normal(item.unit):
        await SD.qty_dasar(conn, tid, pid, item.unit, item.min_quantity, "konversi")  # tanpa konversi -> 400 berkalimat
    if await conn.fetchval(
            """SELECT 1 FROM price_list_items WHERE price_list_id = $1 AND item_id = $2 AND unit IS NOT DISTINCT FROM $3
               AND min_quantity = $4 AND ($5::uuid IS NULL OR id <> $5)""", pl_id, pid, _satuan(item.unit), item.min_quantity, line_id):
        raise GalatBerkode(409, "HARGA_GANDA", "Harga untuk barang, satuan, dan jumlah minimum itu sudah ada di daftar ini.")
    return pid


async def tulis_baris_harga(conn, tid: str, pl_id, item, line_id=None) -> object:
    """SATU penulis baris harga (buat daftar + tambah baris + ubah baris) lewat siapkan_baris_harga. line_id = ubah baris itu di
    tempat (UPDATE; dulu ubah = hapus + tambah, tak atomik). Unik tetap dijaga constraint DB (balapan -> 409 yang sama)."""
    pid = await siapkan_baris_harga(conn, tid, pl_id, item, line_id)
    unit = _satuan(item.unit)
    try:
        async with conn.transaction():
            if line_id is not None:
                return await conn.fetchval(
                    """UPDATE price_list_items SET unit = $3, price = $4, min_quantity = $5, discount_percent = $6, start_date = $7,
                              end_date = $8, is_default_unit = $9
                       WHERE id = $1 AND price_list_id = $2 RETURNING id""",
                    line_id, pl_id, unit, item.price, item.min_quantity, item.discount_percent, item.start_date, item.end_date,
                    item.is_default_unit)
            return await conn.fetchval(
                """INSERT INTO price_list_items (price_list_id, item_id, item_code, unit, price, min_quantity, discount_percent,
                                                 start_date, end_date, is_default_unit)
                   VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10) RETURNING id""",
                pl_id, pid, item.item_code, unit, item.price, item.min_quantity,
                item.discount_percent, item.start_date, item.end_date, item.is_default_unit)
    except asyncpg.exceptions.UniqueViolationError as e:
        raise _galat_unik(e)

async def siapkan_buat_daftar(conn, tid: str, b: CreatePriceListRequest) -> dict:
    """PENENTU BERSAMA POST /price-lists + POST /price-lists/preview (BUG-027, BACA SAJA; kalimat == tulis). -> rencana = medan yang
    disimpan (kode/nama dirapikan spasinya) + baris. Kode unik per usaha (uq_price_lists_tenant_code), satu bawaan aktif
    (uq_price_lists_bawaan), periode, baris lewat siapkan_baris_harga + unik di badan (kunci uq_price_list_items_satuan)."""
    kode, nama = " ".join(b.code.split()), " ".join(b.name.split())
    if not kode:
        raise GalatBerkode(400, "KODE_DAFTAR_KOSONG", "Kode daftar harga wajib diisi.", field="code")
    if not nama:
        raise GalatBerkode(400, "NAMA_DAFTAR_KOSONG", "Nama daftar harga wajib diisi.", field="name")
    if b.start_date and b.end_date and b.end_date < b.start_date:
        raise GalatBerkode(400, "PERIODE_TIDAK_SAH", "Tanggal berakhir tidak boleh sebelum tanggal mulai.", field="end_date")
    if await conn.fetchval("SELECT 1 FROM price_lists WHERE tenant_id = $1 AND code = $2", tid, kode):
        raise _kode_ganda()
    if b.is_default and await conn.fetchval("SELECT 1 FROM price_lists WHERE tenant_id = $1 AND is_default AND is_active", tid):
        raise GalatBerkode(409, "DAFTAR_BAWAAN_GANDA", "Usaha ini sudah punya daftar harga bawaan. Jadikan bawaan yang lama bukan bawaan dulu.",
                           field="is_default")
    baris, kunci = [], set()
    for i, item in enumerate(b.items or []):
        try:
            pid = await siapkan_baris_harga(conn, tid, None, item)
        except GalatBerkode as e:
            raise GalatBerkode(e.status_code, e.code, e.detail, field=f"items.{i}.{e.field}" if e.field else f"items.{i}")
        k = (pid, (_satuan(item.unit) or "").lower(), item.min_quantity)
        if k in kunci:
            raise GalatBerkode(409, "HARGA_GANDA", "Harga untuk barang, satuan, dan jumlah minimum itu sudah ada di daftar ini.",
                               field=f"items.{i}")
        kunci.add(k)
        baris.append({"item_id": str(pid), "item_code": item.item_code, "unit": _satuan(item.unit), "price": item.price,
                      "min_quantity": item.min_quantity, "discount_percent": item.discount_percent, "start_date": item.start_date,
                      "end_date": item.end_date, "is_default_unit": item.is_default_unit})
    return {"code": kode, "name": nama, "price_type": b.price_type, "default_discount": b.default_discount,
            "default_markup": b.default_markup, "start_date": b.start_date, "end_date": b.end_date, "priority": b.priority,
            "description": b.description, "is_default": b.is_default, "items": baris}


logger = logging.getLogger(__name__)
router = APIRouter()


async def get_pool() -> asyncpg.Pool:
    """Get singleton connection pool (Law 32)."""
    from ..services.db_pool import get_db_pool

    return await get_db_pool()


def get_user_context(request: Request) -> dict:
    if not hasattr(request.state, "user") or not request.state.user:
        raise HTTPException(status_code=401, detail="Silakan masuk terlebih dahulu.")
    user = request.state.user
    tenant_id = user.get("tenant_id")
    if not tenant_id:
        raise HTTPException(status_code=401, detail="Sesi tidak valid, silakan masuk lagi.")
    return {
        "tenant_id": tenant_id,
        "user_id": UUID(user.get("user_id")) if user.get("user_id") else None,
    }


@router.get("/health")
async def health_check():
    return {"status": "ok", "service": "price-lists"}


@router.get("/dropdown", response_model=PriceListDropdownResponse)
async def get_dropdown(request: Request):
    try:
        ctx = get_user_context(request)
        pool = await get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT id, code, name, is_default FROM price_lists WHERE tenant_id = $1 AND is_active = true ORDER BY priority, name",
                ctx["tenant_id"],
            )
            return {
                "items": [
                    {
                        "id": str(r["id"]),
                        "code": r["code"],
                        "name": r["name"],
                        "is_default": r["is_default"],
                    }
                    for r in rows
                ]
            }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=GAGAL)


@router.get("", response_model=PriceListListResponse)
async def list_price_lists(
    request: Request,
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    search: Optional[str] = None,
    is_active: Optional[bool] = None,
):
    try:
        ctx = get_user_context(request)
        pool = await get_pool()
        async with pool.acquire() as conn:
            conditions = ["p.tenant_id = $1"]
            params = [ctx["tenant_id"]]
            idx = 2
            if search:
                conditions.append(f"(p.code ILIKE ${idx} OR p.name ILIKE ${idx})")
                params.append(f"%{search}%")
                idx += 1
            if is_active is not None:
                conditions.append(f"p.is_active = ${idx}")
                params.append(is_active)
                idx += 1
            where = " AND ".join(conditions)
            hari_ini = await tanggal_dokumen(conn, ctx["tenant_id"])  # status berlaku: tanggal zona tenant
            total = await conn.fetchval(
                f"SELECT COUNT(*) FROM price_lists p WHERE {where}", *params
            )
            params.extend([limit, skip])
            rows = await conn.fetch(
                f"""
                SELECT p.id, p.code, p.name, p.price_type, p.start_date, p.end_date,
                       p.is_active, p.is_default,
                       (SELECT COUNT(*) FROM price_list_items WHERE price_list_id = p.id) as item_count
                FROM price_lists p WHERE {where} ORDER BY p.priority, p.name LIMIT ${idx} OFFSET ${idx+1}
            """,
                *params,
            )
            return {
                "items": [
                    {
                        "id": str(r["id"]),
                        "code": r["code"],
                        "name": r["name"],
                        "price_type": r["price_type"],
                        "start_date": r["start_date"].isoformat()
                        if r["start_date"]
                        else None,
                        "end_date": r["end_date"].isoformat()
                        if r["end_date"]
                        else None,
                        "is_active": r["is_active"],
                        "is_default": r["is_default"],
                        "item_count": r["item_count"],
                        **HJ.status_daftar(r, hari_ini),
                    }
                    for r in rows
                ],
                "total": total,
                "has_more": skip + limit < total,
            }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=GAGAL)


@router.get("/{price_list_id}", response_model=PriceListDetailResponse)
async def get_price_list(request: Request, price_list_id: UUID):
    try:
        ctx = get_user_context(request)
        pool = await get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM price_lists WHERE id = $1 AND tenant_id = $2",
                price_list_id,
                ctx["tenant_id"],
            )
            if not row:
                raise HTTPException(status_code=404, detail=TAK_ADA)
            items = await conn.fetch(  # nama barang lewat JOIN (satu kueri)
                """SELECT pli.*, p.nama_produk AS item_name FROM price_list_items pli
                   LEFT JOIN products p ON p.id = pli.item_id AND p.tenant_id = $2
                   WHERE pli.price_list_id = $1 ORDER BY pli.item_code, p.nama_produk, pli.min_quantity""",
                price_list_id, ctx["tenant_id"],
            )
            return {
                "success": True,
                "data": {
                    "id": str(row["id"]),
                    "code": row["code"],
                    "name": row["name"],
                    "price_type": row["price_type"],
                    "default_discount": float(row["default_discount"] or 0),
                    "default_markup": float(row["default_markup"] or 0),
                    "start_date": row["start_date"].isoformat()
                    if row["start_date"]
                    else None,
                    "end_date": row["end_date"].isoformat()
                    if row["end_date"]
                    else None,
                    "priority": row["priority"],
                    "description": row["description"],
                    "is_active": row["is_active"],
                    "is_default": row["is_default"],
                    **HJ.status_daftar(row, await tanggal_dokumen(conn, ctx["tenant_id"])),
                    "items": [
                        {
                            "id": str(i["id"]),
                            "item_id": str(i["item_id"]),
                            "item_code": i["item_code"],
                            "item_name": i["item_name"],
                            "unit": i["unit"],
                            "is_default_unit": bool(i["is_default_unit"]),
                            "price": i["price"],
                            "min_quantity": float(i["min_quantity"]),
                            "discount_percent": float(i["discount_percent"])
                            if i["discount_percent"]
                            else None,
                            "start_date": i["start_date"].isoformat()
                            if i["start_date"]
                            else None,
                            "end_date": i["end_date"].isoformat()
                            if i["end_date"]
                            else None,
                            "is_active": i["is_active"],
                        }
                        for i in items
                    ],
                    "created_at": row["created_at"].isoformat(),
                    "updated_at": row["updated_at"].isoformat(),
                },
            }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=GAGAL)


@router.post("/preview")
async def preview_create_price_list(request: Request, body: dict):
    """Pratinjau BUAT daftar harga (BUG-027, pola /customers/preview): penentu SAMA dengan POST /price-lists (siapkan_buat_daftar)
    di transaksi READ ONLY, nol tulis. 200 {success, data:{ok, blocks[{code,message,field?}], rencana, payload}}; badan = badan POST
    byte-identik (payload dikembalikan apa adanya untuk dikirim saat simpan); galat skema -> blok DATA_TIDAK_SAH per medan."""
    from pydantic import ValidationError
    from .customers import blok_dari_galat, blok_validasi
    ctx = get_user_context(request)
    payload = dict(body or {})
    try:
        b = CreatePriceListRequest.model_validate(payload)
    except ValidationError as e:
        return {"success": True, "data": {"ok": False, "blocks": blok_validasi(e), "rencana": None, "payload": payload}}
    bloks, rencana = [], None
    pool = await get_pool()
    async with pool.acquire() as conn:
        tr = conn.transaction(readonly=True)
        await tr.start()
        try:
            try:
                rencana = await siapkan_buat_daftar(conn, ctx["tenant_id"], b)
            except HTTPException as e:
                bloks.append(blok_dari_galat(e))
        finally:
            await tr.rollback()
    return {"success": True, "data": {"ok": not bloks, "blocks": bloks, "rencana": rencana, "payload": payload}}


@router.post("", response_model=PriceListResponse, status_code=201)
async def create_price_list(request: Request, body: CreatePriceListRequest):
    try:
        ctx = get_user_context(request)
        pool = await get_pool()
        async with pool.acquire() as conn:
            try:  # kode / bawaan ganda (uq_price_lists_tenant_code, uq_price_lists_bawaan) -> 409 berkalimat
                async with conn.transaction():
                    r = await siapkan_buat_daftar(conn, ctx["tenant_id"], body)  # BUG-027: penentu SAMA dengan pratinjau
                    pl_id = await conn.fetchval(
                        """INSERT INTO price_lists (tenant_id, code, name, price_type, default_discount, default_markup, start_date,
                                                   end_date, priority, description, is_default, created_by)
                           VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12) RETURNING id""",
                        ctx["tenant_id"], r["code"], r["name"], body.price_type, body.default_discount, body.default_markup,
                        body.start_date, body.end_date, body.priority, body.description, body.is_default, ctx["user_id"],
                    )
                    for item in body.items or []:
                        await tulis_baris_harga(conn, ctx["tenant_id"], pl_id, item)
            except asyncpg.exceptions.UniqueViolationError as e:
                raise _galat_unik(e)
            return {
                "success": True,
                "message": "Daftar harga dibuat.",
                "data": {"id": str(pl_id), "code": r["code"]},
            }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=GAGAL)


@router.patch("/{price_list_id}", response_model=PriceListResponse)
async def update_price_list(
    request: Request, price_list_id: UUID, body: UpdatePriceListRequest
):
    try:
        ctx = get_user_context(request)
        pool = await get_pool()
        async with pool.acquire() as conn:
            existing = await conn.fetchrow(
                "SELECT id, code FROM price_lists WHERE id = $1 AND tenant_id = $2",
                price_list_id,
                ctx["tenant_id"],
            )
            if not existing:
                raise HTTPException(status_code=404, detail=TAK_ADA)
            update_data = body.model_dump(exclude_unset=True)
            if not update_data:
                return {
                    "success": True,
                    "message": "Tidak ada perubahan.",
                    "data": {"id": str(price_list_id)},
                }
            updates, params, idx = [], [], 1
            for field, value in update_data.items():
                updates.append(f"{field} = ${idx}")
                params.append(value)
                idx += 1
            updates.append("updated_at = NOW()")
            params.extend([price_list_id, ctx["tenant_id"]])
            try:
                await conn.execute(
                    f"UPDATE price_lists SET {', '.join(updates)} WHERE id = ${idx} AND tenant_id = ${idx+1}",
                    *params,
                )
            except asyncpg.exceptions.UniqueViolationError as e:  # bawaan / kode ganda -> 409 berkalimat
                raise _galat_unik(e)
            return {
                "success": True,
                "message": "Daftar harga diubah.",
                "data": {"id": str(price_list_id)},
            }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=GAGAL)


@router.delete("/{price_list_id}", response_model=PriceListResponse)
async def delete_price_list(request: Request, price_list_id: UUID):
    try:
        ctx = get_user_context(request)
        pool = await get_pool()
        async with pool.acquire() as conn:
            existing = await conn.fetchrow(
                "SELECT id, code FROM price_lists WHERE id = $1 AND tenant_id = $2",
                price_list_id,
                ctx["tenant_id"],
            )
            if not existing:
                raise HTTPException(status_code=404, detail=TAK_ADA)
            await conn.execute(
                "UPDATE price_lists SET is_active = false, is_default = false, updated_at = NOW() WHERE id = $1",
                price_list_id,
            )
            return {
                "success": True,
                "message": "Daftar harga dinonaktifkan.",
                "data": {"id": str(price_list_id)},
            }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=GAGAL)


@router.post("/{price_list_id}/items", response_model=PriceListResponse)
async def add_item_to_price_list(
    request: Request, price_list_id: UUID, body: PriceListItemCreate
):
    try:
        ctx = get_user_context(request)
        pool = await get_pool()
        async with pool.acquire() as conn:
            existing = await conn.fetchrow(
                "SELECT id FROM price_lists WHERE id = $1 AND tenant_id = $2",
                price_list_id,
                ctx["tenant_id"],
            )
            if not existing:
                raise HTTPException(status_code=404, detail=TAK_ADA)
            item_id = await tulis_baris_harga(conn, ctx["tenant_id"], price_list_id, body)
            return {
                "success": True,
                "message": "Harga ditambahkan ke daftar.",
                "data": {"id": str(item_id)},
            }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=GAGAL)


async def _baris_gabungan(conn, tid: str, price_list_id: UUID, line_id: UUID, body: PriceListItemUpdate, kunci: bool) -> PriceListItemCreate:
    """Daftar milik usaha + baris tersimpan (FOR UPDATE saat tulis) + medan terkirim -> badan lengkap untuk penentu bersama."""
    if not await conn.fetchval("SELECT 1 FROM price_lists WHERE id = $1 AND tenant_id = $2", price_list_id, tid):
        raise HTTPException(status_code=404, detail=TAK_ADA)
    r = await conn.fetchrow("SELECT * FROM price_list_items WHERE id = $1 AND price_list_id = $2" + (" FOR UPDATE" if kunci else ""),
                            line_id, price_list_id)
    if not r:
        raise HTTPException(status_code=404, detail="Baris harga tidak ditemukan.")
    lama = {k: r[k] for k in ("unit", "price", "min_quantity", "is_default_unit", "discount_percent", "start_date", "end_date")}
    lama["discount_percent"] = float(lama["discount_percent"]) if lama["discount_percent"] is not None else None
    return PriceListItemCreate(item_id=str(r["item_id"]), item_code=r["item_code"], **{**lama, **body.model_dump(exclude_unset=True)})


async def _pratinjau_baris(request: Request, price_list_id: UUID, badan, line_id=None) -> dict:
    """Pola /customers/preview: penentu SAMA dengan tulis di transaksi READ ONLY, nol tulis; galat -> blocks (kalimat == tulis)."""
    from pydantic import ValidationError
    from .customers import blok_dari_galat, blok_validasi
    ctx = get_user_context(request)
    payload = dict(badan or {})
    try:
        b = (PriceListItemUpdate if line_id else PriceListItemCreate).model_validate(payload)
    except ValidationError as e:
        return {"success": True, "data": {"ok": False, "blocks": blok_validasi(e), "payload": payload}}
    bloks = []
    pool = await get_pool()
    async with pool.acquire() as conn:
        tr = conn.transaction(readonly=True)
        await tr.start()
        try:
            try:
                if not line_id and not await conn.fetchval("SELECT 1 FROM price_lists WHERE id = $1 AND tenant_id = $2",
                                                           price_list_id, ctx["tenant_id"]):
                    raise HTTPException(status_code=404, detail=TAK_ADA)
                item = await _baris_gabungan(conn, ctx["tenant_id"], price_list_id, line_id, b, False) if line_id else b
                await siapkan_baris_harga(conn, ctx["tenant_id"], price_list_id, item, line_id)
            except HTTPException as e:
                bloks.append(blok_dari_galat(e))
        finally:
            await tr.rollback()
    return {"success": True, "data": {"ok": not bloks, "blocks": bloks, "payload": payload}}


@router.post("/{price_list_id}/items/preview")
async def preview_add_price_list_item(request: Request, price_list_id: UUID, body: dict):
    """Pratinjau tambah baris harga (badan == POST /{id}/items)."""
    return await _pratinjau_baris(request, price_list_id, body)


@router.post("/{price_list_id}/items/{line_id}/edit/preview")
async def preview_update_price_list_item(request: Request, price_list_id: UUID, line_id: UUID, body: dict):
    """Pratinjau ubah baris harga (badan == PATCH /{id}/items/{line_id})."""
    return await _pratinjau_baris(request, price_list_id, body, line_id)


@router.patch("/{price_list_id}/items/{line_id}", response_model=PriceListResponse)
async def update_price_list_item(request: Request, price_list_id: UUID, line_id: UUID, body: PriceListItemUpdate):
    """Ubah SATU baris harga, atomik (ANDROID U3): baris tersimpan + medan terkirim -> penulis YANG SAMA (validasi satuan/unik)."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()
        async with pool.acquire() as conn:
            async with conn.transaction():
                baru = await _baris_gabungan(conn, ctx["tenant_id"], price_list_id, line_id, body, True)
                await tulis_baris_harga(conn, ctx["tenant_id"], price_list_id, baru, line_id=line_id)
            return {"success": True, "message": "Harga diperbarui.", "data": {"id": str(line_id)}}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=GAGAL)


@router.delete("/{price_list_id}/items/{item_id}", response_model=PriceListResponse)
async def remove_item_from_price_list(
    request: Request, price_list_id: UUID, item_id: UUID
):
    try:
        ctx = get_user_context(request)
        pool = await get_pool()
        async with pool.acquire() as conn:
            pl = await conn.fetchrow(
                "SELECT id FROM price_lists WHERE id = $1 AND tenant_id = $2",
                price_list_id,
                ctx["tenant_id"],
            )
            if not pl:
                raise HTTPException(status_code=404, detail=TAK_ADA)
            await conn.execute(
                "DELETE FROM price_list_items WHERE id = $1 AND price_list_id = $2",
                item_id,
                price_list_id,
            )
            return {
                "success": True,
                "message": "Harga dihapus dari daftar.",
                "data": {"id": str(item_id)},
            }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=GAGAL)



@router.post("/resolve/{modul}")
async def resolve_harga(request: Request, body: ResolveHargaRequest,
                       modul: Literal["sales_order", "quote", "sales_invoice", "purchase_order", "bill"]):
    """DAFTAR-HARGA (4 Nov 2026): harga jual SARAN per baris (form SO/SI/Penawaran memanggil tiap barang/satuan/qty/pelanggan/tanggal
    berubah). Penentu SATU services/harga_jual: daftar pelanggan (atau bawaan) x satuan x tingkat qty -> harga per satuan konversi ->
    harga dasar x faktor. READ ONLY. Pengguna boleh menimpa harga di dokumen. {modul} = modul form pemanggil (izin R modul itu,
    middleware); jawaban sama untuk ketiganya."""
    ctx = get_user_context(request)
    tid = ctx["tenant_id"]
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction(readonly=True):
            await conn.execute("SELECT set_config('app.tenant_id', $1, true)", tid)
            arah = "beli" if modul in ("purchase_order", "bill") else "jual"  # BUG-032-BE: harga BELI saran per satuan
            if arah == "jual" and body.customer_id and not await conn.fetchval(
                    "SELECT 1 FROM customers WHERE id = $1 AND tenant_id = $2", body.customer_id, tid):
                raise GalatBerkode(400, "PELANGGAN_TIDAK_DITEMUKAN", "Pelanggan tidak ditemukan di usaha ini.", field="customer_id")
            tgl = body.tanggal or await tanggal_dokumen(conn, tid)
            data = await HJ.harga_saran(conn, tid, body.customer_id if arah == "jual" else None, tgl,
                                        [b.model_dump() for b in body.lines], arah)
    return {"success": True, "data": {"tanggal": tgl.isoformat(), **data}}
