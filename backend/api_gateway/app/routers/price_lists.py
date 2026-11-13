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
from ..services import harga_aksi as HA
from ..services import harga_daftar as HD
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
    if line_id is None:  # tambah baris: barang tak dijual/nonaktif ditolak (mengubah harga baris lama tetap boleh)
        await HA.periksa_barang_dijual(conn, tid, pid)
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
    aksi: Optional[str] = None,
):
    """Daftar + ?aksi= (kedaluwarsa|belum_berlaku|nonaktif|bawaan; services/harga_daftar), urutan stabil (priority, nama, id),
    pelanggan_count + aksi_tersedia per baris, total_baris (Σ server)."""
    try:
        ctx = get_user_context(request)
        if aksi is not None and aksi not in HD.SEMUA_KUNCI:
            raise HTTPException(status_code=400, detail="Saringan tidak dikenal.")
        pool = await get_pool()
        async with pool.acquire() as conn:
            hari_ini = await tanggal_dokumen(conn, ctx["tenant_id"])  # status berlaku: tanggal zona tenant
            conditions = ["p.tenant_id = $1"]
            params = [ctx["tenant_id"]]
            idx = 2
            if aksi in HD.PAKAI_HARI:  # tanggal hanya dipasang bila predikatnya memakainya (parameter tak terpakai = galat asyncpg)
                params.append(hari_ini)
                conditions.append(HD.sql_aksi(aksi, "p", f"${idx}"))
                idx += 1
            elif aksi:
                conditions.append(HD.sql_aksi(aksi, "p", "NULL"))
            if search:
                conditions.append(f"(p.code ILIKE ${idx} OR p.name ILIKE ${idx})")
                params.append(f"%{search}%")
                idx += 1
            if is_active is not None:
                conditions.append(f"p.is_active = ${idx}")
                params.append(is_active)
                idx += 1
            where = " AND ".join(conditions)
            total = await conn.fetchval(
                f"SELECT COUNT(*) FROM price_lists p WHERE {where}", *params
            )
            total_baris = await conn.fetchval(
                f"""SELECT COUNT(*) FROM price_list_items pli JOIN price_lists p ON p.id = pli.price_list_id WHERE {where}""", *params
            )
            params.extend([limit, skip])
            rows = await conn.fetch(
                f"""
                SELECT p.id, p.code, p.name, p.price_type, p.start_date, p.end_date,
                       p.is_active, p.is_default,
                       (SELECT COUNT(*) FROM price_list_items WHERE price_list_id = p.id) as item_count,
                       (SELECT COUNT(*) FROM customers c WHERE c.tenant_id = p.tenant_id AND c.price_list_id = p.id) AS pelanggan_count
                FROM price_lists p WHERE {where} ORDER BY p.priority, p.name, p.id LIMIT ${idx} OFFSET ${idx+1}
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
                        "position_text": HD.position_text(r["is_active"], r["is_default"], HJ.status_daftar(r, hari_ini)["status"]),
                        "pelanggan_count": r["pelanggan_count"],
                        "aksi_tersedia": HD.aksi_tersedia(r, r["pelanggan_count"]),
                    }
                    for r in rows
                ],
                "total": total,
                "total_baris": total_baris,
                "has_more": skip + limit < total,
            }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=GAGAL)


@router.get("/summary")
async def price_lists_summary(request: Request):
    """{kedaluwarsa_count, belum_berlaku_count, nonaktif_count, bawaan_count, total_count} -- predikat SAMA dgn ?aksi= daftar (services/harga_daftar)."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()
        async with pool.acquire() as conn:
            hari_ini = await tanggal_dokumen(conn, ctx["tenant_id"])
            sel = ", ".join(f"COUNT(*) FILTER (WHERE {HD.sql_aksi(k, 'p', '$2')}) AS {k}_count" for k in HD.SEMUA_KUNCI)
            r = await conn.fetchrow(f"SELECT {sel}, COUNT(*) AS total_count FROM price_lists p WHERE p.tenant_id = $1", ctx["tenant_id"], hari_ini)
            return {"success": True, "data": {**{f"{k}_count": r[f"{k}_count"] for k in HD.SEMUA_KUNCI}, "total_count": r["total_count"]}}
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
                """SELECT pli.*, p.nama_produk AS item_name, COALESCE(NULLIF(p.base_unit, ''), p.satuan) AS dasar FROM price_list_items pli
                   LEFT JOIN products p ON p.id = pli.item_id AND p.tenant_id = $2
                   WHERE pli.price_list_id = $1 ORDER BY pli.item_code, p.nama_produk, pli.min_quantity""",
                price_list_id, ctx["tenant_id"],
            )
            hari_ini = await tanggal_dokumen(conn, ctx["tenant_id"])
            st = HJ.status_daftar(row, hari_ini)
            n_pel = await conn.fetchval("SELECT COUNT(*) FROM customers WHERE tenant_id = $1 AND price_list_id = $2", ctx["tenant_id"], price_list_id)
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
                    **st,
                    "position_text": HD.position_text(row["is_active"], row["is_default"], st["status"]),
                    "pelanggan_count": n_pel,
                    "aksi_tersedia": HD.aksi_tersedia(row, n_pel),
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
                            "unit_text": HD.unit_text(i["unit"], i["dasar"]),
                            "berlaku_text": HD.berlaku_text(i["start_date"], i["end_date"]),
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
            async def kerja():
                try:  # kode / bawaan ganda (uq_price_lists_tenant_code, uq_price_lists_bawaan) -> 409 berkalimat
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
                return {"success": True, "message": "Daftar harga dibuat.", "data": {"id": str(pl_id), "code": r["code"]}}
            async with conn.transaction():
                return await HA.idem_aksi(conn, ctx, request, "DH_BUAT", "baru", body.model_dump(mode="json"), kerja)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=GAGAL)


@router.patch("/{price_list_id}", response_model=PriceListResponse)
async def update_price_list(
    request: Request, price_list_id: UUID, body: UpdatePriceListRequest
):
    """Ubah header daftar: penentu SAMA dgn POST /{id}/edit/preview (HA.rencana_ubah) + idempotensi (X-Idempotency-Key)."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()
        async with pool.acquire() as conn:
            update_data = body.model_dump(exclude_unset=True)

            async def kerja():
                existing = await HA.ukur_daftar(conn, ctx["tenant_id"], price_list_id)
                if not existing:
                    raise HTTPException(status_code=404, detail=TAK_ADA)
                if not update_data:
                    return {"success": True, "message": "Tidak ada perubahan.", "data": {"id": str(price_list_id)}}
                kode = " ".join(str(update_data.get("code") or "").split())
                ganda = bool(kode) and await HA.kode_dipakai(conn, ctx["tenant_id"], kode, price_list_id)
                HA.tolak_bila_blok(HA.rencana_ubah(existing, update_data, ganda))
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
                return {"success": True, "message": "Daftar harga diubah.", "data": {"id": str(price_list_id)}}
            async with conn.transaction():
                return await HA.idem_aksi(conn, ctx, request, "DH_UBAH", price_list_id, body.model_dump(mode="json", exclude_unset=True), kerja)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=GAGAL)


@router.delete("/{price_list_id}", response_model=PriceListResponse)
async def delete_price_list(request: Request, price_list_id: UUID):
    """Hapus permanen: DITOLAK bila dipakai pelanggan (menyebut N pelanggan; penentu SAMA dgn POST /{id}/delete/preview). Nonaktifkan = POST /{id}/status."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()
        async with pool.acquire() as conn:
            async def kerja():
                tid = ctx["tenant_id"]
                r = await HA.ukur_daftar(conn, tid, price_list_id)
                HA.tolak_bila_blok(HA.rencana_hapus(r, await HA.ukur_pelanggan(conn, tid, price_list_id), await HA.ukur_baris(conn, tid, price_list_id)))
                await HA.terapkan_hapus(conn, tid, price_list_id)
                return {"success": True, "message": "Daftar harga dihapus.", "data": {"id": str(price_list_id)}}
            async with conn.transaction():
                return await HA.idem_aksi(conn, ctx, request, "DH_HAPUS", price_list_id, {"hapus": True}, kerja)
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
            async def kerja():
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
                    "data": {"id": str(item_id), **await HD.teks_baris(conn, ctx["tenant_id"], item_id)},
                }
            async with conn.transaction():
                return await HA.idem_aksi(conn, ctx, request, "DH_BARIS_TAMBAH", price_list_id, body.model_dump(mode="json"), kerja)
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
            async def kerja():
                baru = await _baris_gabungan(conn, ctx["tenant_id"], price_list_id, line_id, body, True)
                await tulis_baris_harga(conn, ctx["tenant_id"], price_list_id, baru, line_id=line_id)
                return {"success": True, "message": "Harga diperbarui.",
                        "data": {"id": str(line_id), **await HD.teks_baris(conn, ctx["tenant_id"], line_id)}}
            async with conn.transaction():
                return await HA.idem_aksi(conn, ctx, request, "DH_BARIS_UBAH", line_id, body.model_dump(mode="json", exclude_unset=True), kerja)
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
            async def kerja():
                tid = ctx["tenant_id"]
                HA.tolak_bila_blok(HA.rencana_hapus_baris(await HA.ukur_baris_harga(conn, tid, price_list_id, item_id)))
                await conn.execute(
                    "DELETE FROM price_list_items WHERE id = $1 AND price_list_id = $2 AND price_list_id IN (SELECT id FROM price_lists WHERE id = $2 AND tenant_id = $3)",
                    item_id, price_list_id, tid,
                )
                return {"success": True, "message": "Harga dihapus dari daftar.", "data": {"id": str(item_id)}}
            async with conn.transaction():
                return await HA.idem_aksi(conn, ctx, request, "DH_BARIS_HAPUS", item_id, {"hapus": True}, kerja)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=GAGAL)



async def _baca_saja(fungsi):
    """Transaksi READ ONLY untuk pratinjau (nol tulis, rollback)."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        tr = conn.transaction(readonly=True)
        await tr.start()
        try:
            return await fungsi(conn)
        finally:
            await tr.rollback()


def _aktif_dari(body) -> Optional[bool]:
    v = (body or {}).get("aktif")
    return v if isinstance(v, bool) else None


@router.post("/{price_list_id}/status/preview")
async def preview_status_daftar(request: Request, price_list_id: UUID, body: dict):
    """Pratinjau nonaktifkan/aktifkan ({aktif: bool}): peringatan pelanggan terdampak + bawaan; penentu SAMA dgn POST /{id}/status."""
    ctx = get_user_context(request)
    ke = _aktif_dari(body)
    if ke is None:
        return {"success": True, "data": {"ok": False, "blocks": [{"code": "DATA_TIDAK_SAH", "message": "Isi aktif dengan true atau false.", "field": "aktif"}],
                                          "peringatan": [], "rencana": None, "payload": body}}

    async def f(conn):
        r = await HA.ukur_daftar(conn, ctx["tenant_id"], price_list_id)
        return HA.rencana_status(r, await HA.ukur_pelanggan(conn, ctx["tenant_id"], price_list_id), ke)
    return {"success": True, "data": {**await _baca_saja(f), "payload": body}}


@router.post("/{price_list_id}/status")
async def ubah_status_daftar(request: Request, price_list_id: UUID, body: dict):
    try:
        ctx = get_user_context(request)
        ke = _aktif_dari(body)
        if ke is None:
            raise GalatBerkode(400, "DATA_TIDAK_SAH", "Isi aktif dengan true atau false.", field="aktif")
        pool = await get_pool()
        async with pool.acquire() as conn:
            async def kerja():
                tid = ctx["tenant_id"]
                r = await HA.ukur_daftar(conn, tid, price_list_id)
                HA.tolak_bila_blok(HA.rencana_status(r, await HA.ukur_pelanggan(conn, tid, price_list_id), ke))
                await HA.terapkan_status(conn, tid, price_list_id, ke)
                return {"success": True, "message": "Daftar harga diaktifkan." if ke else "Daftar harga dinonaktifkan.",
                        "data": {"id": str(price_list_id), "aktif": ke}}
            async with conn.transaction():
                return await HA.idem_aksi(conn, ctx, request, "DH_STATUS", price_list_id, {"aktif": ke}, kerja)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=GAGAL)


@router.post("/{price_list_id}/default/preview")
async def preview_bawaan_daftar(request: Request, price_list_id: UUID, body: Optional[dict] = None):
    """Pratinjau jadikan bawaan: menyebut bawaan lama yang dicabut; penentu SAMA dgn POST /{id}/default."""
    ctx = get_user_context(request)

    async def f(conn):
        r = await HA.ukur_daftar(conn, ctx["tenant_id"], price_list_id)
        return HA.rencana_bawaan(r, await HA.ukur_bawaan(conn, ctx["tenant_id"]))
    return {"success": True, "data": {**await _baca_saja(f), "payload": body or {}}}


@router.post("/{price_list_id}/default")
async def jadikan_bawaan_daftar(request: Request, price_list_id: UUID, body: Optional[dict] = None):
    try:
        ctx = get_user_context(request)
        pool = await get_pool()
        async with pool.acquire() as conn:
            async def kerja():
                tid = ctx["tenant_id"]
                r = await HA.ukur_daftar(conn, tid, price_list_id)
                HA.tolak_bila_blok(HA.rencana_bawaan(r, await HA.ukur_bawaan(conn, tid)))
                await HA.terapkan_bawaan(conn, tid, price_list_id)
                return {"success": True, "message": "Daftar harga dijadikan bawaan.", "data": {"id": str(price_list_id)}}
            async with conn.transaction():
                return await HA.idem_aksi(conn, ctx, request, "DH_BAWAAN", price_list_id, {"bawaan": True}, kerja)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=GAGAL)


@router.post("/{price_list_id}/delete/preview")
async def preview_hapus_daftar(request: Request, price_list_id: UUID, body: Optional[dict] = None):
    """Pratinjau hapus: DITOLAK bila dipakai pelanggan (N pelanggan); penentu SAMA dgn penghapusan daftar (HA.rencana_hapus)."""
    ctx = get_user_context(request)

    async def f(conn):
        tid = ctx["tenant_id"]
        r = await HA.ukur_daftar(conn, tid, price_list_id)
        return HA.rencana_hapus(r, await HA.ukur_pelanggan(conn, tid, price_list_id), await HA.ukur_baris(conn, tid, price_list_id))
    return {"success": True, "data": {**await _baca_saja(f), "payload": body or {}}}


@router.post("/{price_list_id}/edit/preview")
async def preview_ubah_daftar(request: Request, price_list_id: UUID, body: dict):
    """Pratinjau ubah header: daftar perubahan {medan, dari, ke} + blok (kode ganda, masa berlaku); penentu SAMA dgn PATCH /{id}."""
    from pydantic import ValidationError
    from .customers import blok_validasi
    ctx = get_user_context(request)
    payload = dict(body or {})
    try:
        b = UpdatePriceListRequest.model_validate(payload)
    except ValidationError as e:
        return {"success": True, "data": {"ok": False, "blocks": blok_validasi(e), "peringatan": [], "rencana": None, "payload": payload}}
    dat = b.model_dump(exclude_unset=True)

    async def f(conn):
        tid = ctx["tenant_id"]
        r = await HA.ukur_daftar(conn, tid, price_list_id)
        kode = " ".join(str(dat.get("code") or "").split())
        return HA.rencana_ubah(r, dat, bool(kode) and r is not None and await HA.kode_dipakai(conn, tid, kode, price_list_id))
    return {"success": True, "data": {**await _baca_saja(f), "payload": payload}}


@router.post("/{price_list_id}/items/{line_id}/delete/preview")
async def preview_hapus_baris(request: Request, price_list_id: UUID, line_id: UUID, body: Optional[dict] = None):
    """Pratinjau hapus SATU baris harga (barang, satuan, jumlah minimum, harga); penentu SAMA dgn penghapusan baris."""
    ctx = get_user_context(request)

    async def f(conn):
        return HA.rencana_hapus_baris(await HA.ukur_baris_harga(conn, ctx["tenant_id"], price_list_id, line_id))
    return {"success": True, "data": {**await _baca_saja(f), "payload": body or {}}}


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
