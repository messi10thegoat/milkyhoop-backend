"""
Item Batches Router
===================
Batch/Lot tracking with expiry dates.
Default selection method: FEFO (First Expiry First Out).
"""
from decimal import Decimal
from typing import Optional
from uuid import UUID

import asyncpg
from fastapi import APIRouter, HTTPException, Query, Request, Response

from ..services import batch_aksi as BA
from ..services import batch_saran as BS
from ..services import idem_buat
from ..services import stok_sesuai as SS
from ..services.bayar_keluar_rencana import Blok
from ..services.galat_berkode import GalatBerkode
from ..utils.tanggal_tenant import tanggal_dokumen
from ..schemas.item_batches import (
    AdjustBatchQuantityRequest,
    AdjustBatchResponse,
    AvailableBatch,
    AvailableBatchesResponse,
    BatchWarehouseStock,
    CreateItemBatchRequest,
    CreateItemBatchResponse,
    ExpiringBatchesResponse,
    ExpiredBatchesResponse,
    ItemBatchData,
    ItemBatchDetailData,
    ItemBatchDetailResponse,
    ItemBatchListResponse,
    SaranBatchResponse,
    SetBatchStatusRequest,
    UpdateItemBatchRequest,
    UpdateItemBatchResponse,
)

router = APIRouter()


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


@router.get("", response_model=ItemBatchListResponse)
async def list_item_batches(
    request: Request,
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    item_id: Optional[UUID] = None,
    status: Optional[str] = None,
    search: Optional[str] = None,
    aksi: Optional[str] = Query(None, description="Kunci keadaan (== /summary): semua|aktif|kedaluwarsa|segera_kedaluwarsa|karantina|habis"),
):
    """List all batches (?status= dan ?aksi= memakai penentu keadaan YANG SAMA dgn /summary: batch 'active' yang lewat tanggal terhitung kedaluwarsa)."""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute(
            "SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"]
        )

        where_clauses = ["ib.tenant_id = $1"]
        params = [ctx["tenant_id"]]
        param_idx = 2

        if item_id:
            where_clauses.append(f"ib.item_id = ${param_idx}")
            params.append(item_id)
            param_idx += 1

        hari_ini = await tanggal_dokumen(conn, ctx["tenant_id"])
        if aksi and aksi not in BA.KUNCI:
            raise GalatBerkode(422, "AKSI_TAK_SAH", f"Kunci saringan tidak dikenal. Pilih: {', '.join(BA.KUNCI)}.", field="aksi")
        if status and status not in BA.STATUS_KE_KUNCI:   # status di luar daftar: perilaku lama (kolom persis)
            where_clauses.append(f"ib.status = ${param_idx}")
            params.append(status)
            param_idx += 1
        kunci_saring = ([BA.STATUS_KE_KUNCI[status]] if status in BA.STATUS_KE_KUNCI else []) + ([aksi] if aksi else [])
        pakai_hari = any(k in BA.KUNCI_BERTANGGAL for k in kunci_saring)   # parameter tanggal HANYA bila ada potongan yang memakainya (asyncpg menolak argumen menggantung)
        potongan = [x for x in (BA.syarat(k, f"${param_idx}") for k in kunci_saring) if x]
        if potongan:
            if pakai_hari:
                params.append(hari_ini)
                param_idx += 1
            where_clauses.extend(potongan)

        if search:
            where_clauses.append(f"ib.batch_number ILIKE ${param_idx}")
            params.append(f"%{search}%")
            param_idx += 1

        where_sql = " AND ".join(where_clauses)

        total = await conn.fetchval(
            f"SELECT COUNT(*) FROM item_batches ib WHERE {where_sql}", *params
        )

        rows = await conn.fetch(
            f"""
            SELECT ib.*, i.sku as item_code, i.nama_produk as item_name, i.base_unit
            FROM item_batches ib
            LEFT JOIN products i ON ib.item_id = i.id
            WHERE {where_sql}
            ORDER BY ib.expiry_date ASC NULLS LAST, ib.created_at DESC
            LIMIT ${param_idx} OFFSET ${param_idx + 1}
            """,
            *params,
            limit,
            skip,
        )

        data = [ItemBatchData(**dict(row), **BA.hiasi(dict(row), hari_ini)) for row in rows]
        return ItemBatchListResponse(
            data=data, total=total, has_more=(skip + limit) < total
        )


@router.get("/summary")
async def get_batches_summary(request: Request, item_id: Optional[UUID] = None):
    """Ringkasan keadaan batch -- SATU penentu dgn saringan ?aksi= di daftar: {semua, aktif, kedaluwarsa, segera_kedaluwarsa (bagian dari aktif, 30 hari), karantina, habis}."""
    ctx = get_user_context(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"])
        return {"success": True, "data": await BA.ringkasan(conn, ctx["tenant_id"], await tanggal_dokumen(conn, ctx["tenant_id"]), item_id)}


@router.get("/expiring", response_model=ExpiringBatchesResponse)
async def get_expiring_batches(
    request: Request,
    days: int = Query(30, ge=1, le=365),
    warehouse_id: Optional[UUID] = None,
):
    """Get batches expiring within specified days"""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute(
            "SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"]
        )

        rows = await conn.fetch(
            "SELECT * FROM get_expiring_batches($1, $2, $3)",
            ctx["tenant_id"],
            days,
            warehouse_id,
        )

        total_value = sum(
            row["quantity"] * (row.get("unit_cost", 0) or 0) for row in rows
        )

        return ExpiringBatchesResponse(
            days_ahead=days,
            data=[dict(row) for row in rows],
            total=len(rows),
            total_value=int(total_value),
        )


@router.get("/expired", response_model=ExpiredBatchesResponse)
async def get_expired_batches(
    request: Request,
    warehouse_id: Optional[UUID] = None,
):
    """Get all expired batches"""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute(
            "SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"]
        )

        rows = await conn.fetch(
            "SELECT * FROM get_expired_batches($1, $2)", ctx["tenant_id"], warehouse_id
        )

        total_value = sum((row.get("total_value") or 0 for row in rows), Decimal("0"))

        return ExpiredBatchesResponse(
            data=[dict(row) for row in rows],
            total=len(rows),
            total_value=float(total_value),
        )


@router.get("/{batch_id}", response_model=ItemBatchDetailResponse)
async def get_item_batch(request: Request, batch_id: UUID):
    """Get batch details with warehouse breakdown"""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute(
            "SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"]
        )

        row = await conn.fetchrow(
            """
            SELECT ib.*, i.sku as item_code, i.nama_produk as item_name, i.base_unit
            FROM item_batches ib
            LEFT JOIN products i ON ib.item_id = i.id
            WHERE ib.id = $1 AND ib.tenant_id = $2
            """,
            batch_id,
            ctx["tenant_id"],
        )

        if not row:
            raise HTTPException(status_code=404, detail="Batch not found")

        warehouse_stock = await conn.fetch(
            """
            SELECT bws.warehouse_id, w.name as warehouse_name,
                   bws.quantity, bws.reserved_quantity, bws.available_quantity
            FROM batch_warehouse_stock bws
            JOIN warehouses w ON bws.warehouse_id = w.id
            WHERE bws.batch_id = $1
            """,
            batch_id,
        )

        hari_ini = await tanggal_dokumen(conn, ctx["tenant_id"])
        data = ItemBatchDetailData(
            **dict(row), **BA.hiasi(dict(row), hari_ini),
            warehouse_stock=[BatchWarehouseStock(**dict(ws), **BA.gudang_text(dict(ws), row["base_unit"])) for ws in warehouse_stock],
        )

        return ItemBatchDetailResponse(data=data)


@router.post("/preview")
async def preview_buat_batch(request: Request, body: CreateItemBatchRequest):
    """Pratinjau daftarkan batch (BACA SAJA; penentu BA.rencana_buat == POST /item-batches). Bentuk G3."""
    ctx = get_user_context(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction(readonly=True):
            await conn.execute("SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"])
            bl = Blok(True)
            r = await BA.rencana_buat(conn, ctx["tenant_id"], body, bl, await tanggal_dokumen(conn, ctx["tenant_id"]))
            return BA.pratinjau({"blocks": bl.daftar, "peringatan": r["peringatan"], "rencana": r["rencana"]}, body.model_dump(mode="json"))


@router.post("", response_model=CreateItemBatchResponse)
async def create_item_batch(request: Request, body: CreateItemBatchRequest, response: Response = None):
    """Daftarkan batch baru (Batch C): nomor/kedaluwarsa/mutu SAJA, qty 0, tanpa ledger/jurnal; stok masuk lewat Penerimaan/Penyesuaian.
    Penentu SAMA dgn POST /preview (blok pertama -> galat berkode). X-Idempotency-Key opsional (kunci sama + isi sama = respons asli)."""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"])
            kp, sidik, lama = await idem_buat.mulai(conn, ctx, idem_buat.kunci_dari(request), "ITEM_BATCH_CREATE", body, "item_batches", "batch", response)
            if lama is not None:
                return lama
            await BA.rencana_buat(conn, ctx["tenant_id"], body, Blok(False), await tanggal_dokumen(conn, ctx["tenant_id"]))
            nomor = body.batch_number.strip()
            # t10-tanggal-bisnis: cadangan received_date = hari ini zona tenant.
            received_date = body.received_date or await tanggal_dokumen(conn, ctx["tenant_id"])
            try:
                row = await conn.fetchrow(
                    """
                    INSERT INTO item_batches (
                        tenant_id, item_id, batch_number, manufacture_date, expiry_date,
                        received_date, initial_quantity, current_quantity, unit_cost, total_value,
                        purchase_order_id, bill_id, supplier_batch_number, quality_grade, quality_notes,
                        created_by
                    ) VALUES ($1, $2, $3, $4, $5, $6, 0, 0, 0, 0, $7, $8, $9, $10, $11, $12)
                    RETURNING *
                    """,
                    ctx["tenant_id"], body.item_id, nomor, body.manufacture_date, body.expiry_date, received_date,
                    body.purchase_order_id, body.bill_id, body.supplier_batch_number, body.quality_grade, body.quality_notes,
                    ctx.get("user_id"),
                )
            except asyncpg.UniqueViolationError:   # dua pembuat bersamaan: kendala UNIQUE menang -> 409, bukan 500
                raise GalatBerkode(409, "NOMOR_BATCH_KEMBAR", f"Nomor batch {nomor} sudah dipakai batch lain untuk barang ini.", field="batch_number")

            item = await conn.fetchrow(
                "SELECT sku as code, nama_produk as name FROM products WHERE id = $1 AND tenant_id = $2", body.item_id, ctx["tenant_id"])
            resp = CreateItemBatchResponse(
                data=ItemBatchData(**dict(row), item_code=item["code"] if item else None, item_name=item["name"] if item else None))
            return await idem_buat.simpan(conn, ctx, kp, sidik, "ITEM_BATCH_CREATE", resp, row["id"])


@router.post("/{batch_id}/ubah/preview")
async def preview_ubah_batch(request: Request, batch_id: UUID, body: UpdateItemBatchRequest):
    """Pratinjau ubah batch (BACA SAJA; penentu BA.rencana_ubah == PATCH /{id}). Badan = medan PATCH. Bentuk G3."""
    ctx = get_user_context(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction(readonly=True):
            await conn.execute("SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"])
            bl = Blok(True)
            r = await BA.rencana_ubah(conn, ctx["tenant_id"], batch_id, body, bl, await tanggal_dokumen(conn, ctx["tenant_id"]))
            rencana = {k: v for k, v in r["rencana"].items() if k != "ganti"} if r["rencana"] else None
            return BA.pratinjau({"blocks": bl.daftar, "peringatan": r["peringatan"], "rencana": rencana}, body.model_dump(mode="json"))


@router.post("/{batch_id}/status/preview")
async def preview_status_batch(request: Request, batch_id: UUID, body: SetBatchStatusRequest):
    """Pratinjau karantina / aktifkan (BACA SAJA; penentu BA.rencana_status == POST /status dan PATCH status). Bentuk G3."""
    ctx = get_user_context(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction(readonly=True):
            await conn.execute("SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"])
            bl = Blok(True)
            r = await BA.rencana_status(conn, ctx["tenant_id"], batch_id, body.status, bl, await tanggal_dokumen(conn, ctx["tenant_id"]))
            return BA.pratinjau({"blocks": bl.daftar, "peringatan": r["peringatan"], "rencana": r["rencana"]}, body.model_dump(mode="json"))


@router.post("/{batch_id}/status", response_model=UpdateItemBatchResponse)
async def set_batch_status(request: Request, batch_id: UUID, body: SetBatchStatusRequest):
    """Karantina / aktifkan batch (penentu SAMA dgn pratinjau; blok pertama -> galat berkode)."""
    return await update_item_batch(request, batch_id, UpdateItemBatchRequest(status=body.status))


@router.patch("/{batch_id}", response_model=UpdateItemBatchResponse)
async def update_item_batch(
    request: Request, batch_id: UUID, body: UpdateItemBatchRequest
):
    """Update batch details"""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute(
            "SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"]
        )

        existing = await BA.muat_batch(conn, ctx["tenant_id"], batch_id)
        if not existing:
            raise HTTPException(status_code=404, detail="Batch not found")

        # penentu SAMA dgn POST /{id}/ubah/preview (nomor kembar/terpakai, tanggal, status): blok pertama -> galat berkode, bukan 500
        await BA.rencana_ubah(conn, ctx["tenant_id"], batch_id, body, Blok(False), await tanggal_dokumen(conn, ctx["tenant_id"]))

        updates = []
        params = []
        param_idx = 1

        for field in [
            "batch_number",
            "manufacture_date",
            "expiry_date",
            "supplier_batch_number",
            "quality_grade",
            "quality_notes",
            "status",
        ]:
            value = getattr(body, field, None)
            if field == "batch_number" and value is not None:
                value = value.strip()
            if value is not None:
                updates.append(f"{field} = ${param_idx}")
                params.append(value)
                param_idx += 1

        if not updates:
            return UpdateItemBatchResponse(data=ItemBatchData(**dict(existing)))

        params.append(batch_id)

        try:
            row = await conn.fetchrow(
                f"""
                UPDATE item_batches SET {', '.join(updates)}, updated_at = NOW()
                WHERE id = ${param_idx}
                RETURNING *
                """,
                *params,
            )
        except asyncpg.UniqueViolationError:   # dua pengubah bersamaan memilih nomor yang sama: kendala UNIQUE(tenant,item,batch_number) menang -> 409, bukan 500
            raise GalatBerkode(409, "NOMOR_BATCH_KEMBAR", f"Nomor batch {body.batch_number} sudah dipakai batch lain untuk barang ini.", field="batch_number")

        item = await conn.fetchrow(
            "SELECT sku as code, nama_produk as name, base_unit FROM products WHERE id = $1 AND tenant_id = $2",
            row["item_id"],
            ctx["tenant_id"],
        )
        ekstra = dict(row) | {"base_unit": item["base_unit"] if item else None}

        return UpdateItemBatchResponse(
            data=ItemBatchData(
                **dict(row),
                item_code=item["code"] if item else None,
                item_name=item["name"] if item else None,
                base_unit=item["base_unit"] if item else None,
                **BA.hiasi(ekstra, await tanggal_dokumen(conn, ctx["tenant_id"])),
            )
        )


@router.post("/{batch_id}/adjust/preview")
async def preview_adjust_batch(request: Request, batch_id: UUID, body: AdjustBatchQuantityRequest):
    """Pratinjau penyesuaian batch (BACA SAJA, nol tulis; penentu BA.rencana_sesuaikan == penulis). Bentuk seragam dgn G3 Gudang:
    {success, data:{ok, blocks[{code,message,field}], peringatan[], rencana, payload}}."""
    ctx = get_user_context(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction(readonly=True):
            await conn.execute("SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"])
            bl = Blok(True)
            r = await BA.rencana_sesuaikan(conn, ctx["tenant_id"], batch_id, body, bl)
            b, g = r["batch"], r["gudang"]
            rencana = None
            if b and g:
                u = b["base_unit"]
                rencana = {
                    "batch_number": b["batch_number"], "item_name": b["item_name"], "warehouse_name": g["name"], "quantity_change": str(r["ubah"]),
                    "batch_before": str(r["batch_sebelum"]), "batch_after": str(r["batch_sesudah"]), "stock_before": str(r["stok_sebelum"]), "stock_after": str(r["stok_sesudah"]),
                    "batch_before_text": BA.stock_text(r["batch_sebelum"], u), "batch_after_text": BA.stock_text(r["batch_sesudah"], u),
                    "stock_before_text": BA.stock_text(r["stok_sebelum"], u), "stock_after_text": BA.stock_text(r["stok_sesudah"], u),
                    "ringkas": f"Batch {b['batch_number']} di {g['name']}: {BA.stock_text(r['batch_sebelum'], u)} \u2192 {BA.stock_text(r['batch_sesudah'], u)}",
                    "document_text": "Dibuat dokumen Penyesuaian Stok bernomor + jurnal persediaan + kartu stok.",
                }
            return BA.pratinjau({"blocks": bl.daftar, "peringatan": [], "rencana": rencana}, body.model_dump(mode="json"))


@router.post("/{batch_id}/adjust", response_model=AdjustBatchResponse)
async def adjust_batch_quantity(
    request: Request, batch_id: UUID, body: AdjustBatchQuantityRequest, response: Response = None
):
    """BUG-164 (putusan pemilik 17 Nov): penyesuaian batch = dokumen Penyesuaian Stok bernomor + jurnal persediaan + kartu stok (penulis SAR yang SAMA
    dgn penyesuaian cepat /items/{id}/stock-adjustment). Satu transaksi: kunci (barang, gudang) -> rencana -> draf + posting -> ledger ditandai batch_id
    -> saldo batch di gudang itu bergerak. Pembatalan dokumen SA membalik batch lewat record_inventory_reversal. Respons lama tetap + nomor dokumen."""
    from . import stock_adjustments as SAR
    from ..schemas.stock_adjustments import CreateStockAdjustmentRequest
    from ..services import idem_buat

    ctx = get_user_context(request)
    tid = ctx["tenant_id"]
    pool = await get_pool()
    await SAR._ensure_stock_adj_role_preconditions(pool, tid)

    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.tenant_id', $1, true)", tid)
            _kp, _sd, _lama = await idem_buat.mulai_aksi(conn, ctx, idem_buat.kunci_dari(request), "ITEM_BATCH_ADJ", str(batch_id), body.model_dump(mode="json"), response)
            if _lama is not None:
                return _lama
            pra = await BA.muat_batch(conn, tid, batch_id)
            if not pra:
                raise GalatBerkode(404, "BATCH_TIDAK_DITEMUKAN", "Batch tidak ditemukan.")
            g0 = await SS.gudang(conn, tid, body.warehouse_id, Blok(False))
            # Law 13: kunci per (barang, gudang) SEBELUM membaca stok -- SAMA dgn penyesuaian cepat -- lalu kunci buat per barang
            await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", f"STOCK:{pra['item_id']}:{g0['id']}")
            await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", f"STOCK_ADJ_CREATE:{pra['item_id']}")
            r = await BA.rencana_sesuaikan(conn, tid, batch_id, body, Blok(False), kunci=True)
            b, g, ubah = r["batch"], r["gudang"], r["ubah"]
            tgl = await tanggal_dokumen(conn, tid)  # t10-tanggal-bisnis
            catatan = " - ".join(x for x in (f"Batch {b['batch_number']}", body.reason, body.notes) if x)
            req = CreateStockAdjustmentRequest(
                adjustment_date=tgl, adjustment_type="increase" if ubah > 0 else "decrease", warehouse_id=str(g["id"]), notes=catatan,
                items=[{"product_id": str(b["item_id"]), "quantity_adjustment": ubah, "reason_detail": body.reason}])
            r_buat = await SS.rencana_buat(conn, tid, req, Blok(False))
            sa_id, sa_number, _total = await SAR._buat_draf(conn, ctx, req, r_buat)
            r_post = await SS.rencana_posting(conn, tid, sa_id, Blok(False), kunci=True, counter_account_id=body.counter_account_id or None)
            journal_id, journal_number = await SAR._tulis_posting(conn, ctx, sa_id, r_post)

            # tandai gerak kartu stok dokumen ini dgn batch -> pembatalan SA membalik saldo batch (record_inventory_reversal membaca ledger.batch_id)
            await conn.execute(
                "UPDATE inventory_ledger SET batch_id = $3 WHERE tenant_id = $1 AND source_type = 'STOCK_ADJUSTMENT' AND source_id = $2 AND product_id = $4",
                tid, sa_id, batch_id, b["item_id"])
            # UPDATE dulu: INSERT .. ON CONFLICT dgn jumlah NEGATIF melanggar chk_bws_qty pada baris usulan SEBELUM konflik diperiksa (kurang selalu gagal).
            diubah = await conn.execute(
                "UPDATE batch_warehouse_stock SET quantity = quantity + $4, last_movement_date = NOW(), updated_at = NOW() "
                "WHERE tenant_id = $1 AND batch_id = $2 AND warehouse_id = $3", tid, batch_id, g["id"], ubah)
            if diubah.endswith(" 0"):   # belum ada baris batch di gudang itu: hanya penambahan yang lolos rencana
                await conn.execute(
                    "INSERT INTO batch_warehouse_stock (tenant_id, batch_id, warehouse_id, quantity, last_movement_date) VALUES ($1, $2, $3, $4, NOW())",
                    tid, batch_id, g["id"], ubah)
            await conn.execute(
                "UPDATE item_batches SET status = 'depleted' WHERE id = $1 AND tenant_id = $2 AND current_quantity <= 0 AND status = 'active'",
                batch_id, tid)
            # BUG-455: penyesuaian NAIK pada batch yang sudah habis ('depleted') mengaktifkannya lagi, kalau tidak FEFO melewatinya
            # (get_available_batches hanya membaca status 'active') dan 'tersedia' kurang dari Σ batch.
            await conn.execute(
                "UPDATE item_batches SET status = 'active' WHERE id = $1 AND tenant_id = $2 AND current_quantity > 0 AND status = 'depleted' "
                "AND (expiry_date IS NULL OR expiry_date >= $3)", batch_id, tid, tgl)   # t10-tanggal-bisnis: tanggal bisnis tenant

            row = await conn.fetchrow("SELECT * FROM item_batches WHERE id = $1 AND tenant_id = $2", batch_id, tid)
            ws = await conn.fetchrow(
                """
                SELECT bws.*, w.name as warehouse_name
                FROM batch_warehouse_stock bws
                JOIN warehouses w ON bws.warehouse_id = w.id
                WHERE bws.batch_id = $1 AND bws.warehouse_id = $2
                """,
                batch_id, g["id"])
            hasil = AdjustBatchResponse(
                data=ItemBatchData(
                    **dict(row), item_code=b["item_code"], item_name=b["item_name"], base_unit=b["base_unit"],
                    **BA.hiasi(dict(row) | {"base_unit": b["base_unit"]}, tgl)),
                warehouse_stock=BatchWarehouseStock(**dict(ws), **BA.gudang_text(dict(ws), b["base_unit"])),
                adjustment_id=sa_id, adjustment_number=sa_number, journal_id=journal_id, journal_number=journal_number)
            return await idem_buat.simpan(conn, ctx, _kp, _sd, "ITEM_BATCH_ADJ", hasil.model_dump(mode="json"), sa_id)


@router.get(
    "/items/{item_id}/batches/available", response_model=AvailableBatchesResponse
)
async def get_available_batches_for_item(
    request: Request,
    item_id: UUID,
    warehouse_id: UUID = Query(...),
    quantity: Decimal = Query(..., gt=0),
    method: str = Query("FEFO", pattern="^(FEFO|FIFO)$"),
):
    """Get available batches for selection using FEFO or FIFO"""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute(
            "SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"]
        )

        rows = await conn.fetch(
            "SELECT * FROM get_available_batches($1, $2, $3, $4, $5)",
            ctx["tenant_id"],
            item_id,
            warehouse_id,
            quantity,
            method,
        )

        total_available = sum(row["available_quantity"] for row in rows)
        total_allocated = sum(row["quantity_to_use"] for row in rows)

        # t10-tanggal-bisnis: sisa hari kedaluwarsa dihitung dari hari ini
        # zona tenant (date.today() = UTC, 00-07 WIB meleset satu hari).
        hari_ini = await tanggal_dokumen(conn, ctx["tenant_id"])
        batches = []
        for row in rows:
            days_until = None
            if row["expiry_date"]:
                days_until = (row["expiry_date"] - hari_ini).days

            batches.append(
                AvailableBatch(
                    batch_id=row["batch_id"],
                    batch_number=row["batch_number"],
                    expiry_date=row["expiry_date"],
                    days_until_expiry=days_until,
                    available_quantity=row["available_quantity"],
                    quantity_to_use=row["quantity_to_use"],
                    unit_cost=row["unit_cost"],
                )
            )

        return AvailableBatchesResponse(
            item_id=item_id,
            warehouse_id=warehouse_id,
            quantity_requested=quantity,
            quantity_available=total_available,
            quantity_allocated=total_allocated,
            fully_satisfied=total_allocated >= quantity,
            method=method,
            batches=batches,
        )


@router.get("/items/{item_id}/saran", response_model=SaranBatchResponse)
async def saran_batch_item(
    request: Request,
    item_id: UUID,
    quantity: Decimal = Query(..., gt=0),
    method: str = Query("FEFO", pattern="^(FEFO|FIFO)$"),
    warehouse_id: Optional[UUID] = None,
):
    """Saran batch lintas gudang (warehouse_id opsional = semua gudang). Baca saja."""
    ctx = get_user_context(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"])
        p = await conn.fetchrow(
            "SELECT track_batches, base_unit FROM products WHERE id = $1 AND tenant_id = $2", item_id, ctx["tenant_id"])
        if not p:
            raise GalatBerkode(404, "BARANG_TIDAK_ADA", "Barang tidak ditemukan.", field="item_id")
        if not p["track_batches"]:
            return SaranBatchResponse(track_batches=False, **BS.tanpa_batch(quantity, p["base_unit"]))
        hari_ini = await tanggal_dokumen(conn, ctx["tenant_id"])
        rows = await conn.fetch(BS.SQL_BATCH, ctx["tenant_id"], item_id, warehouse_id)
        return SaranBatchResponse(track_batches=True, **BS.susun([dict(r) for r in rows], quantity, method, hari_ini, p["base_unit"]))
