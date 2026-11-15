"""
Warehouses Router
=================
Multi-warehouse/location management endpoints.
"""
from typing import Optional
from uuid import UUID

import asyncpg
from fastapi import APIRouter, HTTPException, Query, Request, Response

from ..services import gudang_daftar as GD
from ..services import gudang_rencana as GR  # G3: SATU penentu pratinjau + penulis
from ..services import idem_buat
from ..schemas.warehouses import (
    CreateWarehouseRequest,
    CreateWarehouseResponse,
    DeleteWarehouseResponse,
    LowStockResponse,
    SetWarehouseStatusRequest,
    UpdateWarehouseRequest,
    UpdateWarehouseResponse,
    WarehouseData,
    WarehouseDetailResponse,
    WarehouseListResponse,
    WarehouseStockResponse,
    WarehouseStockValueResponse,
)

router = APIRouter()

# BUG-077: nilai stok per gudang = qty gudang x WAC BARANG (WAC MH per barang, bukan per gudang; fungsi yang sama dengan
# jalur COGS). Rupiah dibulatkan 0 dp di titik lahir per baris, dihitung dari WAC 2 dp (bukan dari unit_cost yang
# sudah dibulatkan). Satu ekspresi untuk /stock dan /stock-value ($2 = tenant_id di kedua query) -- kini di services/gudang_daftar
# (dipakai juga nilai_stok daftar standar SO).
WAC_GUDANG, NILAI_GUDANG = GD.WAC_GUDANG, GD.NILAI_GUDANG

# Connection pool


async def get_pool() -> asyncpg.Pool:
    """Get singleton connection pool (Law 32)."""
    from ..services.db_pool import get_db_pool

    return await get_db_pool()


def get_user_context(request: Request) -> dict:
    if not hasattr(request.state, "user"):
        raise HTTPException(status_code=401, detail="Silakan masuk terlebih dahulu.")
    return {
        "tenant_id": request.state.user["tenant_id"],
        "user_id": request.state.user.get("user_id"),
    }


# ============================================================================
# ENDPOINTS
# ============================================================================


@router.get("", response_model=WarehouseListResponse)
async def list_warehouses(
    request: Request,
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    search: Optional[str] = None,
    is_active: Optional[bool] = None,
    is_branch: Optional[bool] = None,
    aksi: Optional[str] = Query(None, pattern="^(stok_minus|nonaktif)$", description="Kunci standar SO (services/gudang_daftar)"),
    with_totals: bool = Query(False, description="sum_nilai = Σ nilai stok gudang terfilter"),
):
    """List all warehouses (+ ?aksi=stok_minus, with_totals, position_text/aksi/nilai_stok per baris)."""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute(
            "SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"]
        )

        where_clauses = ["tenant_id = $1", "NOT is_system"]  # K8: gudang sistem (Transit) tak tampil
        params = [ctx["tenant_id"]]
        param_idx = 2

        if search:
            where_clauses.append(
                f"(code ILIKE ${param_idx} OR name ILIKE ${param_idx})"
            )
            params.append(f"%{search}%")
            param_idx += 1

        if is_active is not None:
            where_clauses.append(f"is_active = ${param_idx}")
            params.append(is_active)
            param_idx += 1

        if is_branch is not None:
            where_clauses.append(f"is_branch = ${param_idx}")
            params.append(is_branch)
            param_idx += 1

        if aksi in GD.AKSI:
            where_clauses.append(GD.sql_aksi(aksi))

        where_sql = " AND ".join(where_clauses)

        # Count total
        total = await conn.fetchval(
            f"SELECT COUNT(*) FROM warehouses WHERE {where_sql}", *params
        )

        sum_nilai = None
        if with_totals is True:  # Query() default = objek truthy saat dipanggil langsung -> `is True`
            sum_nilai = int(await conn.fetchval(f"SELECT COALESCE(SUM({GD.sql_nilai()}), 0) FROM warehouses WHERE {where_sql}", *params))

        # Fetch warehouses
        rows = await conn.fetch(
            f"""
            SELECT *, {GD.kolom_turunan()} FROM warehouses
            WHERE {where_sql}
            ORDER BY is_default DESC, name ASC, id ASC
            LIMIT ${param_idx} OFFSET ${param_idx + 1}
            """,
            *params,
            limit,
            skip,
        )

        data = [WarehouseData(**{**dict(row), **GD.turunan(row)}, position_text=GD.position_text(row["is_active"], row["is_default"], row["is_branch"]))
                for row in rows]

        return WarehouseListResponse(data=data, total=total, sum_nilai=sum_nilai)


# Standar SO: WAJIB di atas /{warehouse_id} (rute terdaftar duluan menang)
@router.get("/summary")
async def warehouses_summary(request: Request):
    """{stok_minus_count, nonaktif_count, total_count} -- predikat SAMA dgn ?aksi= / is_active daftar (services/gudang_daftar)."""
    ctx = get_user_context(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        r = await conn.fetchrow(f"SELECT COUNT(*) FILTER (WHERE {GD.sql_stok_minus()}) AS stok_minus_count, "
                                f"COUNT(*) FILTER (WHERE NOT is_active) AS nonaktif_count, COUNT(*) AS total_count "
                                f"FROM warehouses WHERE tenant_id = $1 AND NOT is_system", ctx["tenant_id"])
    return {"success": True, "data": {k: int(r[k]) for k in ("stok_minus_count", "nonaktif_count", "total_count")}}


@router.get("/low-stock", response_model=LowStockResponse)
async def get_low_stock_items(
    request: Request,
    warehouse_id: Optional[UUID] = None,
    limit: int = Query(100, ge=1, le=500),
):
    """Get items below reorder level"""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute(
            "SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"]
        )

        where_extra = ""
        params = [ctx["tenant_id"]]
        if warehouse_id:
            where_extra = " AND ws.warehouse_id = $2"
            params.append(warehouse_id)

        rows = await conn.fetch(
            f"""
            SELECT
                ws.item_id, i.code as item_code, i.name as item_name,
                ws.warehouse_id, w.name as warehouse_name,
                ws.quantity, ws.reorder_level,
                (ws.reorder_level - ws.quantity) as shortage
            FROM warehouse_stock ws
            JOIN items i ON ws.item_id = i.id
            JOIN warehouses w ON ws.warehouse_id = w.id
            WHERE ws.tenant_id = $1
            AND ws.reorder_level IS NOT NULL
            AND ws.quantity <= ws.reorder_level
            {where_extra}
            ORDER BY shortage DESC
            LIMIT ${len(params) + 1}
            """,
            *params,
            limit,
        )

        return LowStockResponse(data=[dict(row) for row in rows], total=len(rows))


@router.get("/{warehouse_id}", response_model=WarehouseDetailResponse)
async def get_warehouse(request: Request, warehouse_id: UUID):
    """Get warehouse details"""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute(
            "SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"]
        )

        row = await conn.fetchrow(
            f"SELECT *, {GD.kolom_turunan()} FROM warehouses WHERE id = $1 AND tenant_id = $2 AND NOT is_system",
            warehouse_id,
            ctx["tenant_id"],
        )

        if not row:
            raise HTTPException(status_code=404, detail="Warehouse not found")

        return WarehouseDetailResponse(data=WarehouseData(
            **{**dict(row), **GD.turunan(row)}, position_text=GD.position_text(row["is_active"], row["is_default"], row["is_branch"]),
            barang_minus=await GD.barang_minus(conn, ctx["tenant_id"], warehouse_id) if row["stok_minus"] else [],  # K7 + P5
            aksi_tersedia=await GD.aksi_tersedia(conn, ctx["tenant_id"], row)))


async def _gudang(conn, tid: str, warehouse_id):
    w = await conn.fetchrow("SELECT * FROM warehouses WHERE id = $1 AND tenant_id = $2", warehouse_id, tid)
    if not w:
        raise HTTPException(status_code=404, detail="Warehouse not found")
    return dict(w)


async def _data(conn, tid: str, row) -> WarehouseData:
    """Data gudang sesudah tulis (+ position_text, aksi_tersedia, jenis_text dari server)."""
    return WarehouseData(**dict(row), position_text=GD.position_text(row["is_active"], row["is_default"], row["is_branch"]),
                         aksi_tersedia=await GD.aksi_tersedia(conn, tid, row))


async def _tulis_nilai(conn, tid: str, warehouse_id, nilai: dict):
    """UPDATE bersaring tenant atas nilai rencana (nama kolom dari daftar tetap GR.URUT, bukan masukan pengguna)."""
    kolom = [k for k in GR.URUT if k in nilai]
    sets = ", ".join(f"{k} = ${i}" for i, k in enumerate(kolom, 1))
    return await conn.fetchrow(f"UPDATE warehouses SET {sets}, updated_at = NOW() WHERE id = ${len(kolom) + 1} AND tenant_id = ${len(kolom) + 2} RETURNING *",
                               *[nilai[k] for k in kolom], warehouse_id, tid)


@router.post("/preview")
async def preview_create_warehouse(request: Request, body: CreateWarehouseRequest):
    """G3: pratinjau BUAT (read-only; penentu SAMA dgn POST): blok, peringatan, rencana (+ kode otomatis, jenis_text), payload."""
    ctx = get_user_context(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        return GR.pratinjau(await GR.rencana_buat(conn, ctx["tenant_id"], body.model_dump()), body.model_dump())


@router.post("/{warehouse_id}/edit/preview")
async def preview_edit_warehouse(request: Request, warehouse_id: UUID, body: UpdateWarehouseRequest):
    """G3: pratinjau UBAH (badan = PATCH): null/tak dikirim = tak diubah, "" = kosongkan."""
    ctx = get_user_context(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        w = await _gudang(conn, ctx["tenant_id"], warehouse_id)
        return GR.pratinjau(await GR.rencana_ubah(conn, ctx["tenant_id"], w, body.model_dump(exclude_unset=True)), body.model_dump(exclude_unset=True))


@router.post("/{warehouse_id}/status/preview")
async def preview_status_warehouse(request: Request, warehouse_id: UUID, body: SetWarehouseStatusRequest):
    """G3: pratinjau aktifkan/nonaktifkan (penentu SAMA dgn POST /status dan DELETE)."""
    ctx = get_user_context(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        w = await _gudang(conn, ctx["tenant_id"], warehouse_id)
        return GR.pratinjau(await GR.rencana_status(conn, ctx["tenant_id"], w, body.aktif))


@router.post("/{warehouse_id}/set-default/preview")
async def preview_set_default_warehouse(request: Request, warehouse_id: UUID):
    """G3: pratinjau jadikan utama (penentu SAMA dgn POST /set-default)."""
    ctx = get_user_context(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        w = await _gudang(conn, ctx["tenant_id"], warehouse_id)
        return GR.pratinjau(await GR.rencana_utama(conn, ctx["tenant_id"], w))


@router.post("", response_model=CreateWarehouseResponse)
async def create_warehouse(request: Request, body: CreateWarehouseRequest, response: Response = None):
    """Create a new warehouse (G3: penentu bersama pratinjau; kode otomatis GDG-NNN bila kosong; X-Idempotency-Key opsional)."""
    ctx = get_user_context(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"])
            kp, sidik, lama = await idem_buat.mulai(conn, ctx, idem_buat.kunci_dari(request), "WH_CREATE", body, "warehouses", "gudang", response)
            if lama is not None:
                return lama
            await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", f"GUDANG_KODE:{ctx['tenant_id']}")   # kode otomatis tak bentrok antar permintaan
            r = await GR.rencana_buat(conn, ctx["tenant_id"], body.model_dump())
            GR.tolak(r["blocks"])
            n = r["nilai"]
            try:
                row = await conn.fetchrow(
                    """INSERT INTO warehouses (tenant_id, code, name, address, city, province, postal_code, country, phone, email, manager_name,
                                               is_default, is_active, is_branch, branch_code, created_by)
                       VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16) RETURNING *""",
                    ctx["tenant_id"], n["code"], n["name"], n["address"], n["city"], n["province"], n["postal_code"], n["country"], n["phone"], n["email"],
                    n["manager_name"], n["is_default"], n["is_active"], n["is_branch"], n["branch_code"], ctx.get("user_id"))
            except asyncpg.UniqueViolationError:
                raise GR.GalatBerkode(400, "GUDANG_KODE_SUDAH_ADA", f"Kode gudang '{n['code']}' sudah dipakai.", "code")
            resp = {"success": True, "message": "Warehouse created successfully", "data": (await _data(conn, ctx["tenant_id"], row)).model_dump(mode="json")}
            return await idem_buat.simpan(conn, ctx, kp, sidik, "WH_CREATE", resp, row["id"])


@router.patch("/{warehouse_id}", response_model=UpdateWarehouseResponse)
async def update_warehouse(request: Request, warehouse_id: UUID, body: UpdateWarehouseRequest):
    """Update warehouse details (G3: penentu bersama pratinjau; null = tak diubah, "" = kosongkan)."""
    ctx = get_user_context(request)
    pool = await get_pool()
    # Optimistic concurrency (opt-in If-Match): reject a stale write.
    from ..services.optimistic_concurrency import assert_if_match_row
    await assert_if_match_row(request, "warehouses", warehouse_id)

    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"])
            w = await _gudang(conn, ctx["tenant_id"], warehouse_id)
            r = await GR.rencana_ubah(conn, ctx["tenant_id"], w, body.model_dump(exclude_unset=True))
            GR.tolak(r["blocks"])
            row = await _tulis_nilai(conn, ctx["tenant_id"], warehouse_id, r["nilai"]) if r["nilai"] else await conn.fetchrow(
                "SELECT * FROM warehouses WHERE id = $1 AND tenant_id = $2", warehouse_id, ctx["tenant_id"])
            return UpdateWarehouseResponse(data=await _data(conn, ctx["tenant_id"], row))


@router.delete("/{warehouse_id}", response_model=DeleteWarehouseResponse)
async def delete_warehouse(request: Request, warehouse_id: UUID):
    """Deactivate warehouse (soft delete) = alias /status {aktif:false} (penentu SAMA; peringatan ber-stok = urusan pratinjau FE)."""
    ctx = get_user_context(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"])
            w = await _gudang(conn, ctx["tenant_id"], warehouse_id)
            r = await GR.rencana_status(conn, ctx["tenant_id"], w, False, blok_berstok=True)   # jalur LAMA: gudang ber-stok tetap ditolak
            GR.tolak(r["blocks"])
            await _tulis_nilai(conn, ctx["tenant_id"], warehouse_id, r["nilai"])
            return DeleteWarehouseResponse()


@router.post("/{warehouse_id}/status")
async def set_warehouse_status(request: Request, warehouse_id: UUID, body: SetWarehouseStatusRequest, response: Response = None):
    """G3: PENULIS aktifkan/nonaktifkan (penentu SAMA dgn pratinjau); X-Idempotency-Key opsional; keadaan sama = blok GUDANG_SUDAH_*."""
    ctx = get_user_context(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"])
            kp, sd, lama = await idem_buat.mulai_aksi(conn, ctx, idem_buat.kunci_dari(request), "WH_STATUS", warehouse_id, lambda: {"aktif": body.aktif}, response)
            if lama is not None:
                return lama
            w = await _gudang(conn, ctx["tenant_id"], warehouse_id)
            r = await GR.rencana_status(conn, ctx["tenant_id"], w, body.aktif)
            GR.tolak(r["blocks"])
            row = await _tulis_nilai(conn, ctx["tenant_id"], warehouse_id, r["nilai"])
            resp = {"success": True, "message": "Gudang diaktifkan" if body.aktif else "Gudang dinonaktifkan",
                    "data": (await _data(conn, ctx["tenant_id"], row)).model_dump(mode="json")}
            return await idem_buat.simpan(conn, ctx, kp, sd, "WH_STATUS", resp, warehouse_id) if kp else resp


@router.get("/{warehouse_id}/stock", response_model=WarehouseStockResponse)
async def get_warehouse_stock(
    request: Request,
    warehouse_id: UUID,
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    search: Optional[str] = None,
    hanya_berstok: bool = Query(True, description="BUG-230: True (bawaan) = hanya baris ber-stok (quantity <> 0) = barang yang dihitung strip detail + stock-value; False = ikut baris stok 0"),
):
    """Get stock in warehouse. BUG-230: bawaan hanya barang ber-stok, supaya jumlah baris (total) == jumlah barang di detail/stock-value."""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute(
            "SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"]
        )

        # Verify warehouse
        wh = await conn.fetchrow(
            "SELECT id, name FROM warehouses WHERE id = $1 AND tenant_id = $2 AND NOT is_system",
            warehouse_id,
            ctx["tenant_id"],
        )
        if not wh:
            raise HTTPException(status_code=404, detail="Warehouse not found")

        where_extra = " AND ws.quantity <> 0" if hanya_berstok else ""
        params = [warehouse_id, ctx["tenant_id"]]
        if search:
            where_extra += " AND (i.item_code ILIKE $3 OR i.nama_produk ILIKE $3)"
            params.append(f"%{search}%")

        total = await conn.fetchval(
            f"""
            SELECT COUNT(*) FROM warehouse_stock ws
            JOIN products i ON ws.item_id = i.id
            WHERE ws.warehouse_id = $1 AND ws.tenant_id = $2 {where_extra}
            """,
            *params,
        )

        rows = await conn.fetch(
            f"""
            SELECT
                ws.item_id, i.item_code, i.nama_produk as item_name,
                ws.quantity, ws.reserved_quantity, ws.available_quantity,
                i.base_unit as unit, ROUND(w.wac, 0)::BIGINT as unit_cost,
                {NILAI_GUDANG}::BIGINT as total_value,
                ws.reorder_level, ws.reorder_quantity, ws.last_stock_date
            FROM warehouse_stock ws
            JOIN products i ON ws.item_id = i.id
            {WAC_GUDANG}
            WHERE ws.warehouse_id = $1 AND ws.tenant_id = $2 {where_extra}
            ORDER BY i.nama_produk ASC
            LIMIT ${len(params) + 1} OFFSET ${len(params) + 2}
            """,
            *params,
            limit,
            skip,
        )

        return WarehouseStockResponse(
            data=[dict(row) for row in rows],
            total=total,
            warehouse_id=warehouse_id,
            warehouse_name=wh["name"],
        )


@router.get("/{warehouse_id}/stock-value", response_model=WarehouseStockValueResponse)
async def get_warehouse_stock_value(request: Request, warehouse_id: UUID):
    """Get total stock value in warehouse"""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute(
            "SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"]
        )

        wh = await conn.fetchrow(
            "SELECT id, name FROM warehouses WHERE id = $1 AND tenant_id = $2 AND NOT is_system",
            warehouse_id,
            ctx["tenant_id"],
        )
        if not wh:
            raise HTTPException(status_code=404, detail="Warehouse not found")

        row = await conn.fetchrow(
            f"""
            SELECT
                COUNT(DISTINCT ws.item_id)::INT as total_items,
                COALESCE(SUM(ws.quantity), 0) as total_quantity,
                COALESCE(SUM({NILAI_GUDANG}), 0)::BIGINT as total_value
            FROM warehouse_stock ws
            JOIN products p ON ws.item_id = p.id
            {WAC_GUDANG}
            WHERE ws.warehouse_id = $1 AND ws.tenant_id = $2 AND ws.quantity > 0
            """,
            warehouse_id,
            ctx["tenant_id"],
        )

        return WarehouseStockValueResponse(
            warehouse_id=warehouse_id,
            warehouse_name=wh["name"],
            total_items=row["total_items"],
            total_quantity=row["total_quantity"],
            total_value=row["total_value"],
        )



@router.post("/{warehouse_id}/set-default")
async def set_default_warehouse(request: Request, warehouse_id: UUID, response: Response = None):
    """Set warehouse as default (G3: penentu bersama pratinjau; sudah utama = sukses diam; X-Idempotency-Key opsional)."""
    ctx = get_user_context(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"])
            kp, sd, lama = await idem_buat.mulai_aksi(conn, ctx, idem_buat.kunci_dari(request), "WH_UTAMA", warehouse_id, lambda: {}, response)
            if lama is not None:
                return lama
            w = await _gudang(conn, ctx["tenant_id"], warehouse_id)
            r = await GR.rencana_utama(conn, ctx["tenant_id"], w)
            GR.tolak(r["blocks"])
            if r["nilai"]:
                # gudang utama lama dicabut trigger DB trg_single_default_warehouse (diukur 9 Nov)
                await conn.execute("UPDATE warehouses SET is_default = true WHERE id = $1 AND tenant_id = $2", warehouse_id, ctx["tenant_id"])
            resp = {"success": True, "message": "Warehouse set as default"}
            return await idem_buat.simpan(conn, ctx, kp, sd, "WH_UTAMA", resp, warehouse_id) if kp else resp
