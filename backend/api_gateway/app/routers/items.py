"""
Items Router - Master Data for Goods and Services

This router provides CRUD operations for items (products/services).
Items are the master data used in sales and purchase transactions.

Endpoints:
- GET  /items              - List all items
- GET  /items/{id}         - Get item detail
- POST /items              - Create item
- PUT  /items/{id}         - Update item
- DELETE /items/{id}       - Delete item
- GET  /items/units        - Get available units
- POST /items/units        - Create custom unit
- GET  /items/accounts     - Get accounts for dropdowns
- GET  /items/taxes        - Get tax options
"""

from ..services.cari_kata import syarat_kata
from fastapi import APIRouter, HTTPException, Request, Query, Response, UploadFile, File
from typing import Optional
import logging
import asyncpg
import json
from uuid import UUID
from datetime import date as dateclass
from decimal import Decimal

from ..services import teks_galat as tg
from ..services import satuan_master as SM
from ..services import impor_barang as IB  # BUG-021 impor Excel + 1 baris chatbox
from ..services import idem_buat
from ..services import barang_aksi as BA  # P3 (13 Nov): keadaan, pratinjau status/hapus/duplikat/kategori, daftar jejak
from ..services import barang_teks as BT  # BUG-235/236: jenis_text, nama tampil, deskripsi Barang
from ..services import barang_daftar as BD  # standar SO: satu penentu kunci aksi/teks posisi (7 Nov)
from ..services import stok_baris as SB
from ..services import stok_sesuai as SS_PEN  # penyesuaian-2: akun lawan pilihan divalidasi (SATU penentu)
from ..services.bayar_keluar_rencana import Blok as BlokPen
from ..services.satuan_dasar import faktor_json as SD_faktor_json
from ..services.kosakata_ledger import BATAL_JUAL, KELUAR_JUAL, KELUAR_JUAL_FAKTUR, sql_daftar
from ..utils.tanggal_tenant import tanggal_dokumen
from ..services.saldo_awal_stok import catat_saldo_awal_stok, periksa_saldo_awal
from ..schemas.items import (
    CreateItemRequest,
    UpdateItemRequest,
    CreateItemResponse,
    UpdateItemResponse,
    DeleteItemResponse,
    ItemListResponse,
    ItemListItem,
    ItemListConversion,
    ItemDetailResponse,
    UnitListResponse,
    CreateUnitRequest,
    CreateUnitResponse,
    AccountsResponse,
    AccountOption,
    TaxOptionsResponse,
    TaxOption,
    ItemsSummaryResponse,
    ItemsStatsResponse,
    ItemsStatsStockResponse,
    CoaAccountOption,
    CoaAccountsResponse,
    ItemTransaction,
    ItemTransactionsResponse,
    RelatedDocument,
    ItemRelatedResponse,
    CreateCategoryRequest,
    CreateCategoryResponse,
    CategoryListResponse,
    ItemActivity,
    ItemActivityResponse,
    DEFAULT_UNITS,
    SALES_ACCOUNTS,
    PURCHASE_ACCOUNTS,
)
from ..config import settings
from ..services.resolve_account import resolve_account_id
from ..services.role_resolver import (
    AccountRole,
    AccountRoleUnmappedError,
    resolve_account_id_by_role,
)

logger = logging.getLogger(__name__)
router = APIRouter()


# =============================================================================
# DATABASE HELPERS
# =============================================================================


async def get_db_connection():
    """Get database connection using environment variables"""
    db_config = settings.get_db_config()
    return await asyncpg.connect(**db_config)


SEN = Decimal("0.01")


def _rp(v) -> Decimal:
    """Rupiah 2 desimal (Law 9/25); None -> 0."""
    return Decimal(str(v or 0)).quantize(SEN)


def hpp_dan_margin(nilai, stok, harga_jual) -> dict:
    """BUG-038 (6 Nov 2026): WAC per satuan dasar = nilai persediaan ledger / stok (sumber SAMA dengan stock_value di detail dan
    inventoryValue di stats); stok <= 0 -> None. margin_amount = harga jual - WAC; margin_pct = margin / harga jual x 100 (1 desimal,
    rumus FE lama). Tanpa harga jual / tanpa WAC -> None."""
    stok = Decimal(str(stok or 0))
    wac = (Decimal(str(nilai or 0)) / stok).quantize(SEN) if stok > 0 and nilai is not None else None
    jual = Decimal(str(harga_jual)) if harga_jual is not None else None
    if wac is None or not jual or jual <= 0:
        return {"average_cost": wac, "margin_amount": None, "margin_pct": None}
    m = (jual - wac).quantize(SEN)
    return {"average_cost": wac, "margin_amount": m, "margin_pct": (m / jual * 100).quantize(Decimal("0.1"))}


def get_tenant_id(request: Request) -> str:
    """Extract tenant_id from authenticated request"""
    if not hasattr(request.state, "user") or not request.state.user:
        raise HTTPException(status_code=401, detail="Silakan masuk terlebih dahulu.")

    tenant_id = request.state.user.get("tenant_id")
    if not tenant_id:
        raise HTTPException(status_code=401, detail="Sesi tidak valid, silakan masuk lagi.")

    return tenant_id


async def _auto_default_product_accounts(
    conn,
    tenant_id: str,
    item_type,
    track_inventory,
    inventory_account_id,
    cogs_account_id,
):
    """Auto-default stockable-goods inventory/COGS accounts to the tenant's
    role-mapped accounts when the caller left them NULL.

    Makes the COGS/restock path's previously-silent role fallback explicit at the
    master-data layer (same accounts, zero economic change). Stockable goods only
    (item_type='goods' AND track_inventory=true). If the tenant lacks the role
    mapping, leave NULL + WARN rather than hard-failing the create/update.

    Returns (inventory_account_id, cogs_account_id) — resolved or unchanged.
    """
    if item_type != "goods" or not track_inventory:
        return inventory_account_id, cogs_account_id

    if not inventory_account_id:
        try:
            inventory_account_id = str(
                await resolve_account_id_by_role(
                    conn, tenant_id, AccountRole.INVENTORY_MERCHANDISE
                )
            )
        except AccountRoleUnmappedError:
            logger.warning(
                "Auto-default skipped: tenant %s has no INVENTORY_MERCHANDISE role "
                "mapping; leaving inventory_account_id NULL on stockable product",
                tenant_id,
            )
    if not cogs_account_id:
        try:
            cogs_account_id = str(
                await resolve_account_id_by_role(
                    conn, tenant_id, AccountRole.COGS_SALES
                )
            )
        except AccountRoleUnmappedError:
            logger.warning(
                "Auto-default skipped: tenant %s has no COGS_SALES role mapping; "
                "leaving cogs_account_id NULL on stockable product",
                tenant_id,
            )
    return inventory_account_id, cogs_account_id


# Connection pool for newer endpoints


async def get_pool():
    """Get singleton connection pool (Law 32)."""
    from ..services.db_pool import get_db_pool

    return await get_db_pool()


# 11 Okt 2026 (MASTER): nama barang ganda (sesama barang belum dihapus, V402 idx_products_tenant_nama) -> 409 berkode.
# Pesan SENGAJA tanpa nama barang dan tanpa kata kode/sku/barcode/satuan/unit/harga: dua pemeta FE (barang.ts
# petakanGalatBarang, useDataForm petakanGalatItem) memilih medan dari ISI pesan -- kata itu memindahkan galat ke medan lain.
KODE_NAMA_BARANG = "NAMA_BARANG_DIPAKAI"
PESAN_NAMA_BARANG = "Nama ini sudah dipakai barang lain di Persediaan. Gunakan nama lain."
INDEKS_NAMA_BARANG = "idx_products_tenant_nama"


# W-20261013-ITEMFIELD: SEMUA 409 barang berbentuk {code, field, message, terhapus, nonaktif, item_id?}.
SQL_PEMEGANG = "id, status, (deleted_at IS NOT NULL) AS terhapus"


def _rincian_pemegang(pemegang) -> dict:
    """`item_id` HANYA bila pemegang nonaktif dan belum terhapus (FE menawarkan "Aktifkan kembali"). Pemegang terhapus
    tanpa id: kode/barcode tidak dipakai ulang (putusan pemilik 11 Okt)."""
    if not pemegang:
        return {"terhapus": False, "nonaktif": False}
    terhapus = bool(pemegang["terhapus"])
    nonaktif = not terhapus and pemegang["status"] == "inactive"
    r = {"terhapus": terhapus, "nonaktif": nonaktif}
    if nonaktif:
        r["item_id"] = str(pemegang["id"])
    return r


def galat_nama_barang_dipakai(pemegang=None) -> HTTPException:
    return HTTPException(status_code=409, detail={
        "code": KODE_NAMA_BARANG, "field": "name", "message": PESAN_NAMA_BARANG, **_rincian_pemegang(pemegang)})


# 11 Okt 2026 (MASTER via WORKSPACE): kode/SKU barang dipakai -> detail berkode (pola NAMA_BARANG_DIPAKAI). `terhapus`
# = pemegang kode itu baris yang sudah dihapus (deleted_at terisi; indeks V072 mencakup baris terhapus, kode TIDAK
# dipakai ulang -- putusan pemilik). Pesan teks SENGAJA tetap seperti semula.
KODE_KODE_BARANG = "KODE_BARANG_DIPAKAI"
KODE_BARCODE_BARANG = "BARCODE_BARANG_DIPAKAI"
INDEKS_KODE_BARANG = {"idx_products_tenant_sku": "sku", "idx_products_tenant_item_code": "item_code"}


def galat_kode_barang_dipakai(sku: str, terhapus: bool = False, field: str = "sku", pemegang=None) -> HTTPException:
    rincian = _rincian_pemegang(pemegang) if pemegang is not None else {"terhapus": bool(terhapus), "nonaktif": False}
    return HTTPException(status_code=409, detail={
        "code": KODE_KODE_BARANG, "field": field, "message": f"Kode item {sku} sudah digunakan", **rincian})


def galat_barcode_barang_dipakai(pemegang=None) -> HTTPException:
    # Pesan lama dipertahankan: pemeta FE memilih medan barcode dari kata "barcode".
    return HTTPException(status_code=409, detail={
        "code": KODE_BARCODE_BARANG, "field": "barcode", "message": "Item with this barcode already exists",
        **_rincian_pemegang(pemegang)})


def nama_barang_bentrok(e: BaseException) -> bool:
    """Balapan: cek app lolos, indeks DB menolak -> tetap 409 berkode, bukan 500."""
    return isinstance(e, asyncpg.UniqueViolationError) and getattr(e, "constraint_name", None) == INDEKS_NAMA_BARANG


def galat_bentrok_unik(e: BaseException, body) -> Optional[HTTPException]:
    """Indeks unik barang (nama/sku/item_code) menolak -> 409 berkode, bukan 500 ber-teks galat DB."""
    if nama_barang_bentrok(e):
        return galat_nama_barang_dipakai()
    if isinstance(e, asyncpg.UniqueViolationError):
        field = INDEKS_KODE_BARANG.get(getattr(e, "constraint_name", None))
        if field:
            nilai = getattr(body, field, None)
            if not nilai:  # kode OTOMATIS bentrok dengan penulis serentak lain (bukan salah pengguna): 409 berkode, coba lagi
                return HTTPException(status_code=409, detail={"code": "KODE_BARANG_BENTROK", "field": field, "message":
                                     "Kode barang otomatis bentrok dengan pembuatan barang lain yang serentak. Coba lagi."})
            return galat_kode_barang_dipakai(nilai, field=field)
    return None


KODE_PAJAK_ITEM = "PAJAK_ITEM_TIDAK_SAH"


async def nama_pajak_item(conn, tenant_id: str, tax_id, field: str, arah: str) -> Optional[str]:
    """tax_codes.id pajak item (otoritas) -> nama pajak untuk kolom teks sales_tax/purchase_tax. Milik tenant (Law 24),
    aktif, dan arah cocok (output = jual, input = beli; arah kosong = PPh/Tanpa Pajak, boleh). None bila id kosong."""
    if tax_id is None:
        return None
    tc = await conn.fetchrow(
        "SELECT name, direction, is_active FROM tax_codes WHERE id = $1 AND tenant_id = $2", tax_id, tenant_id)
    if not tc:
        salah = "tidak ditemukan"
    elif not tc["is_active"]:
        salah = "tidak aktif"
    elif tc["direction"] and tc["direction"] != arah:
        salah = "bukan pajak penjualan" if arah == "output" else "bukan pajak pembelian"
    else:
        return tc["name"]
    raise HTTPException(status_code=422, detail={"code": KODE_PAJAK_ITEM, "field": field, "message": f"Pajak item {salah}."})


KODE_FIELD_BELUM_DIDUKUNG = "FIELD_BELUM_DIDUKUNG"


def periksa_badan_item(body, aksi: str) -> None:
    """Field belum didukung yang BERISI -> 422 (tak ada jalur jual/beli yang menerapkannya; menyimpannya = janji palsu,
    membuangnya = kehilangan diam). Kunci tak dikenal -> WARNING saja (pemanggil chat dibangun LLM; daftar pemanggil
    tak terbukti lengkap) -- 422 untuk kunci asing = unit tersendiri sesudah log bersih."""
    f = body.field_belum_didukung()
    if f:
        raise HTTPException(status_code=422, detail={
            "code": KODE_FIELD_BELUM_DIDUKUNG, "field": f, "message": f"Isian {f} belum didukung. Kosongkan isian ini."})
    if body.kunci_asing:
        logger.warning("[ITEM_KUNCI_ASING] %s kunci tak dikenal dibuang: %s", aksi, body.kunci_asing)


def get_user_context(request):
    """Extract and validate user context from request."""
    if not hasattr(request.state, "user") or not request.state.user:
        raise HTTPException(status_code=401, detail="Silakan masuk terlebih dahulu.")

    user = request.state.user
    tenant_id = user.get("tenant_id")
    if not tenant_id:
        raise HTTPException(status_code=401, detail="Sesi tidak valid, silakan masuk lagi.")

    return {
        "tenant_id": tenant_id,
        "user_id": user.get("user_id"),
        "username": user.get("username") or user.get("email"),
    }


# =============================================================================
# LIST ITEMS
# =============================================================================


def _syarat_cari(search, idx: int):
    """Cari per kata (nama/barcode), SATU predikat untuk daftar dan /summary -> (sql "AND (...)" | "", params)."""
    return syarat_kata(search, ("p.nama_produk", "p.barcode"), idx)   # BUG-181: SATU fungsi per-kata + escape (services/cari_kata)


@router.get("/items", response_model=ItemListResponse)
async def list_items(
    request: Request,
    item_type: Optional[str] = Query(
        None, description="Filter by type: goods, service"
    ),
    track_inventory: Optional[bool] = Query(
        None, description="Filter by track_inventory"
    ),
    search: Optional[str] = Query(None, description="Search by name or barcode"),
    kategori: Optional[str] = Query(None, description="Filter by category"),
    stock_status: Optional[str] = Query(
        None, description="Filter by stock status: in_stock, low_stock, out_of_stock"
    ),
    status: Optional[str] = Query(
        None, description="Filter by status: active, inactive"
    ),
    sort_by: Optional[str] = Query(
        "created_at",
        description="Sort column: created_at, name, sku, sales_price, purchase_price, current_stock",
    ),
    sort_order: Optional[str] = Query(
        "desc", description="Sort direction: asc or desc"
    ),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    aksi: Optional[str] = Query(None, description="Kunci aksi: stok_habis, di_bawah_titik_pesan, tanpa_harga_jual, nonaktif"),
    with_totals: bool = Query(False, description="Sertakan sum_nilai_stok (Σ server baris tersaring)"),
):
    """
    List all items with optional filtering.

    Use track_inventory=true to get only items that are tracked in inventory.
    """
    tenant_id = get_tenant_id(request)
    conn = None

    try:
        conn = await get_db_connection()

        # Build query with filters - include conversions as JSON array
        # Stock data derived from inventory_ledger (Law 16 compliance)
        query_parts = [
            """SELECT p.*, COALESCE(per.jumlah, 0) as current_stock, per.total_nilai as stock_value, per.transaction_count,
            v.name as vendor_name,
            """ + BD.sql_aksi("di_bawah_titik_pesan") + """ as low_stock,
            COALESCE(
                (SELECT json_agg(json_build_object(
                    'conversion_unit', uc.conversion_unit,
                    'conversion_factor', uc.conversion_factor,
                    'sales_price', uc.sales_price,
                    'purchase_price', uc.purchase_price
                ) ORDER BY uc.conversion_factor)
                FROM unit_conversions uc
                WHERE uc.product_id = p.id AND uc.tenant_id = p.tenant_id AND uc.is_active = true),
                '[]'::json
            ) as conversions"""
        ]
        query_parts.append("FROM products p")
        query_parts.append(
            """LEFT JOIN LATERAL (
                            SELECT COALESCE(SUM(il.quantity_in) - SUM(il.quantity_out), 0) as jumlah,
                            COALESCE(SUM(il.total_cost * SIGN(il.quantity_in - il.quantity_out)), 0) as total_nilai,
                            (
                                SELECT COUNT(*) FROM (
                                    SELECT 1 FROM inventory_ledger WHERE product_id = p.id AND tenant_id = p.tenant_id
                                    UNION ALL
                                    SELECT 1 FROM sales_invoice_items WHERE item_id = p.id
                                    UNION ALL
                                    SELECT 1 FROM bill_items WHERE product_id = p.id
                                    UNION ALL
                                    SELECT 1 FROM stock_adjustment_items WHERE product_id = p.id
                                    UNION ALL
                                    SELECT 1 FROM stock_transfer_items WHERE item_id = p.id
                                    UNION ALL
                                    SELECT 1 FROM production_orders WHERE product_id = p.id AND tenant_id = p.tenant_id
                                    UNION ALL
                                    SELECT 1 FROM recipes WHERE product_id = p.id AND tenant_id = p.tenant_id
                                ) txns
                            ) as transaction_count
                            FROM inventory_ledger il
                            WHERE il.product_id = p.id AND il.tenant_id = CAST(p.tenant_id AS TEXT)
                        ) per ON true"""
        )
        query_parts.append(
            "LEFT JOIN vendors v ON v.id::text = p.preferred_vendor_id::text AND v.tenant_id = p.tenant_id"
        )
        query_parts.append("WHERE p.tenant_id = $1 AND p.deleted_at IS NULL")

        params = [tenant_id]
        param_idx = 2

        if item_type:
            query_parts.append(f"AND p.item_type = ${param_idx}")
            params.append(item_type)
            param_idx += 1

        if track_inventory is not None:
            query_parts.append(f"AND p.track_inventory = ${param_idx}")
            params.append(track_inventory)
            param_idx += 1

        if kategori:
            query_parts.append(f"AND p.kategori = ${param_idx}")
            params.append(kategori)
            param_idx += 1

        if status:
            query_parts.append(f"AND p.status = ${param_idx}")
            params.append(status)
            param_idx += 1

        cari_sql, cari_params = _syarat_cari(search, param_idx)
        if cari_sql:
            query_parts.append(cari_sql)
            params.extend(cari_params)
            param_idx += len(cari_params)

        # stock_status lama = predikat BD yang sama dengan pill/kartu (dulu tanpa syarat aktif -> nyawit 19 vs kartu 11)
        if isinstance(stock_status, str) and stock_status:
            query_parts.append("AND " + {"in_stock": BD.sql_ada_stok(), "low_stock": BD.sql_aksi("di_bawah_titik_pesan"),
                                         "out_of_stock": BD.sql_aksi("stok_habis")}.get(stock_status, BD.sql_dilacak()))
        if isinstance(aksi, str) and aksi:
            if aksi not in BD.SEMUA_KUNCI:
                raise HTTPException(status_code=422, detail=f"aksi tidak dikenal: {aksi}")
            query_parts.append("AND " + BD.sql_aksi(aksi))

        # Count total (+ Σ nilai stok server bila with_totals)
        count_parts = [part for part in query_parts if not part.startswith("SELECT")]
        count_query = f"SELECT COUNT(*) AS total, {BD.SQL_NILAI_STOK} AS nilai FROM products p " + " ".join(count_parts[1:])
        hitung_row = await conn.fetchrow(count_query, *params)
        total = hitung_row["total"]

        # Add ordering and pagination (whitelisted, prevents SQL injection)
        SORT_COLUMN_WHITELIST = {
            "created_at": "p.created_at",
            "updated_at": "p.updated_at",
            "name": "p.nama_produk",
            "sku": "p.item_code",
            "sales_price": "p.sales_price",
            "purchase_price": "p.purchase_price",
            "current_stock": "COALESCE(per.jumlah, 0)",
        }
        sort_col = SORT_COLUMN_WHITELIST.get((sort_by or "").lower(), "p.created_at")
        sort_dir = "ASC" if (sort_order or "").lower() == "asc" else "DESC"
        # Tiebreaker p.id DESC for stable pagination
        query_parts.append(f"ORDER BY {sort_col} {sort_dir}, p.id DESC")
        query_parts.append(f"LIMIT ${param_idx} OFFSET ${param_idx + 1}")
        params.extend([limit, offset])

        # Execute query
        query = " ".join(query_parts)
        rows = await conn.fetch(query, *params)
        # bisa dijual per halaman: SATU panggilan stok_baris (kueri tetap, bukan N+1) untuk barang dilacak saja
        dilacak_ids = [row["id"] for row in rows if BD.dilacak(row)]
        stok_halaman = (await SB.hitung(conn, tenant_id, dilacak_ids, await tanggal_dokumen(conn, tenant_id)))["barang"] \
            if dilacak_ids else {}

        # Transform rows to response
        items = []
        for row in rows:
            sb = stok_halaman.get(str(row["id"])) or {}
            # Parse conversions from JSON
            conversions_data = row.get("conversions") or []
            if isinstance(conversions_data, str):
                conversions_data = json.loads(conversions_data)

            conversions = (
                [
                    ItemListConversion(
                        conversion_unit=c["conversion_unit"],
                        conversion_factor=c["conversion_factor"],
                        purchase_price=c.get("purchase_price"),
                        sales_price=c.get("sales_price"),
                    )
                    for c in conversions_data
                ]
                if conversions_data
                else []
            )

            items.append(
                ItemListItem(
                    id=row["id"],
                    name=row["nama_produk"],
                    item_type=row.get("item_type", "goods"),
                    track_inventory=row.get("track_inventory", True),
                    bisa_dikirim=bool(row.get("bisa_dikirim") or False),
                    base_unit=row.get("base_unit") or row["satuan"],
                    barcode=row.get("barcode"),
                    kategori=row.get("kategori"),
                    deskripsi=row.get("deskripsi"),
                    is_returnable=row.get("is_returnable", True),
                    track_batches=row.get("track_batches", False),
                    track_expiry=row.get("track_expiry", False),
                    default_expiry_days=row.get("default_expiry_days"),
                    sales_price=row.get("sales_price") or row.get("harga_jual"),
                    purchase_price=row.get("purchase_price"),
                    image_url=row.get("image_url"),
                    reorder_level=row["reorder_level"]
                    if row.get("reorder_level")
                    else None,
                    reorder_point=int(row["reorder_level"])
                    if row.get("reorder_level")
                    else 0,
                    vendor_name=row.get("vendor_name"),
                    sales_tax=row.get("sales_tax"),
                    purchase_tax=row.get("purchase_tax"),
                    status=row.get("status", "active"),
                    item_code=row.get("item_code"),
                    sku=row.get("sku"),
                    costing_method=row.get("costing_method", "weighted_average"),
                    for_sales=row.get("for_sales", True),
                    for_purchases=row.get("for_purchases", True),
                    current_stock=row.get("current_stock"),
                    stock_value=row.get("stock_value"),
                    transaction_count=int(row.get("transaction_count", 0)),
                    low_stock=row.get("low_stock", False),
                    conversions=conversions,
                    created_at=row["created_at"],
                    updated_at=row["updated_at"],
                    aksi_semua=BD.aksi_semua(row, row.get("current_stock")),
                    keadaan=BA.keadaan(row), keadaan_text=BA.keadaan_text(row),
                    position_text=BD.position_text(row, row.get("current_stock"), sb.get("bisa_dijual"),
                                                   sb.get("satuan_dasar") or row.get("base_unit") or row.get("satuan")),
                    position_text_ket=BD.position_text_ket(row, row.get("current_stock"), sb.get("bisa_dijual"),
                                                           sb.get("satuan_dasar") or row.get("base_unit") or row.get("satuan")),
                    bisa_dijual=sb.get("bisa_dijual"),
                )
            )

        return ItemListResponse(
            success=True,
            items=items,
            total=total or 0,
            has_more=(offset + limit) < (total or 0),
            sum_nilai_stok=_rp(hitung_row["nilai"]) if with_totals is True else None,
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error listing items: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if conn:
            await conn.close()


# =============================================================================
# CREATE ITEM
# =============================================================================


# Urutan pemecah seri jenis bawaan (jumlah sama): goods > non_inventory > service.
_URUT_JENIS = ("goods", "non_inventory", "service")


def pilih_jenis_bawaan(hitungan: dict) -> str:
    """Jenis item bawaan tenant = jenis TERBANYAK di item aktif; tenant tanpa item -> 'goods'. Murni (diuji)."""
    calon = [(hitungan.get(j, 0), -i, j) for i, j in enumerate(_URUT_JENIS) if hitungan.get(j, 0) > 0]
    return max(calon)[2] if calon else "goods"


async def bawaan_panel_barang(conn, tenant_id: str) -> dict:
    """10 Okt 2026 (MASTER, F3 panel Barang inline): belum ada setelan tenant eksplisit (diukur: tak ada kolom), jadi
    diturunkan dari item AKTIF tenant -- grapgrap -> non_inventory + bisa_dikirim (pemilik sengaja), kaos -> goods.
    default_bisa_dikirim = MAYORITAS (lebih dari separuh) item non_inventory aktif bisa_dikirim; tanpa non_inventory -> False."""
    rows = await conn.fetch(
        """SELECT item_type, count(*) AS n, count(*) FILTER (WHERE bisa_dikirim) AS kirim
           FROM products WHERE tenant_id = $1 AND deleted_at IS NULL GROUP BY item_type""",
        tenant_id)
    jenis = pilih_jenis_bawaan({r["item_type"]: r["n"] for r in rows if r["item_type"]})
    ni = next((r for r in rows if r["item_type"] == "non_inventory"), None)
    return {"default_item_type": jenis, "default_bisa_dikirim": bool(ni and ni["kirim"] * 2 > ni["n"])}


def tanpa_stok_bila_bukan_barang(body) -> None:
    """non_inventory/service = TANPA stok: track_inventory dipaksa False. Skema berdefault True dan validatornya hanya
    menolkan jasa (dan tak jalan untuk nilai default) -> POST non_inventory tanpa medan ini dulu tersimpan
    track_inventory=true, padahal faktur memilih jalur stok/HPP/penyerahan dari track_inventory. 10 Okt."""
    if body.item_type != "goods":
        body.track_inventory = False


async def _kode_otomatis(conn, tenant_id: str, item_type: str) -> str:
    """Kode barang otomatis BERIKUTNYA (PREFIX-NNNN = nomor terbesar + 1; termasuk yang sudah dihapus). Dipanggil sekali oleh
    _siapkan_buat_barang (pratinjau/validasi) dan ULANG oleh tulis_barang_baru di bawah kunci per tenant (balapan kode, 12 Nov)."""
    # Determine prefix based on item_type (Indonesian standard)
    prefix_map = {
        "goods": "BRG",
        "service": "JSA",
        "non_inventory": "NIN",
    }
    prefix = prefix_map.get(item_type, "ITM")

    # Get next number (include soft-deleted to avoid constraint violation)
    # Use regex to match only standard format PREFIX-NNNN (not PREFIX-XXX-NNN variants)
    row = await conn.fetchrow(
        """
        SELECT item_code FROM products
        WHERE tenant_id = $1 AND item_code ~ ('^' || $2 || '-[0-9]+$')
        ORDER BY CAST(SUBSTRING(item_code FROM '[0-9]+$') AS INTEGER) DESC LIMIT 1
        """,
        tenant_id,
        prefix,
    )

    import re as re_module

    if row and row["item_code"]:
        match = re_module.search(r"([0-9]+)$", row["item_code"])
        if match:
            num = int(match.group(1)) + 1
        else:
            num = 1
    else:
        num = 1

    return f"{prefix}-{num:04d}"


async def _siapkan_buat_barang(conn, tenant_id: str, ctx: dict, body: CreateItemRequest) -> tuple:
    """AS-G14 (Milkybook T0, 24 Okt 2026): aturan BUAT barang SATU untuk pratinjau dan POST /items (pratinjau == tulis). Baca saja;
    galat = HTTPException SAMA dengan sebelum refaktor (409 NAMA/KODE/BARCODE_BARANG_DIPAKAI, pajak, saldo awal stok). Mengisi
    body.sales_tax/purchase_tax/akun persediaan-HPP seperti dulu. -> (item_code, sku)."""
    # Check for duplicate name (barang aktif saja: V402 indeks unik nama parsial WHERE deleted_at IS NULL)
    existing = await conn.fetchrow(
        f"SELECT {SQL_PEMEGANG} FROM products WHERE tenant_id = $1 AND nama_produk = $2 AND deleted_at IS NULL",
        tenant_id,
        body.name,
    )
    if existing:
        raise galat_nama_barang_dipakai(existing)

    # Check for duplicate barcode
    if body.barcode:
        existing_barcode = await conn.fetchrow(
            # Sapuan tenant 26 Sep 2026: dulu lintas-tenant -> 409 palsu + oracle keberadaan.
            f"SELECT {SQL_PEMEGANG} FROM products WHERE barcode = $1 AND tenant_id = $2 "
            "ORDER BY (deleted_at IS NULL) DESC LIMIT 1",
            body.barcode, tenant_id,
        )
        if existing_barcode:
            raise galat_barcode_barang_dipakai(existing_barcode)

    # Auto-generate item_code if not provided
    item_code_to_use = body.item_code
    sku_to_use = body.sku

    if not item_code_to_use:
        item_code_to_use = await _kode_otomatis(conn, tenant_id, body.item_type)

    # SKU defaults to item_code if not provided
    if not sku_to_use:
        sku_to_use = item_code_to_use

    # Check for duplicate SKU (after SKU is finalized)
    existing_sku = await conn.fetchrow(
        f"SELECT {SQL_PEMEGANG} FROM products "
        "WHERE tenant_id = $1 AND sku = $2 ORDER BY (deleted_at IS NULL) DESC LIMIT 1",
        tenant_id,
        sku_to_use,
    )
    # Pemeriksaan ganda HANYA untuk nilai dari PENGGUNA (kode/SKU eksplisit): kode OTOMATIS dihitung ULANG di bawah kunci oleh
    # tulis_barang_baru; kode otomatis yang kebetulan sudah diambil penulis serentak bukan salah pengguna (harness cari-1, 12 Nov).
    if existing_sku and (body.sku or body.item_code):
        raise galat_kode_barang_dipakai(sku_to_use, field="sku", pemegang=existing_sku)

    # Check for duplicate item_code (after item_code is finalized)
    existing_item_code = await conn.fetchrow(
        f"SELECT {SQL_PEMEGANG} FROM products WHERE tenant_id = $1 AND item_code = $2 "
        "ORDER BY (deleted_at IS NULL) DESC LIMIT 1",
        tenant_id,
        item_code_to_use,
    )
    if existing_item_code and body.item_code:
        raise galat_kode_barang_dipakai(item_code_to_use, field="item_code", pemegang=existing_item_code)

    # Pajak item: id = otoritas, nama teks diisi dari tax_codes (W-20261013-ITEMFIELD; dulu id dibuang diam).
    if body.sales_tax_id is not None:
        body.sales_tax = await nama_pajak_item(conn, tenant_id, body.sales_tax_id, "sales_tax_id", "output")
    if body.purchase_tax_id is not None:
        body.purchase_tax = await nama_pajak_item(conn, tenant_id, body.purchase_tax_id, "purchase_tax_id", "input")

    # Auto-default inventory/COGS accounts for stockable goods so the
    # COGS/restock path no longer relies on an implicit silent role fallback.
    (
        body.inventory_account_id,
        body.cogs_account_id,
    ) = await _auto_default_product_accounts(
        conn,
        tenant_id,
        body.item_type,
        body.track_inventory,
        body.inventory_account_id,
        body.cogs_account_id,
    )

    # Saldo awal stok: harga pokok diperiksa SEBELUM produk berdiri (audit F1 10 Okt 2026; services/saldo_awal_stok.py).
    if body.track_inventory and body.item_type == "goods":
        await periksa_saldo_awal(
            conn,
            tenant_id,
            body.opening_stock,
            body.opening_stock_rate if body.opening_stock_rate else (body.purchase_price or 0),
        )
    return item_code_to_use, sku_to_use


@router.post("/items/preview")
async def preview_create_item(request: Request, body: dict):
    """Pratinjau BUAT barang (AS-G14): penentu SAMA dengan POST /items (tanpa_stok_bila_bukan_barang + periksa_badan_item +
    _siapkan_buat_barang), transaksi READ ONLY, nol tulis. 200 {success, data:{ok, blocks, rencana{..., efek[]}, payload}}. Galat
    skema (mis. jasa + bisa_dikirim, validator schemas/items.py) -> blok DATA_TIDAK_SAH per medan (bukan 422)."""
    from pydantic import ValidationError
    from .customers import blok_dari_galat, blok_validasi
    ctx = get_user_context(request)
    payload = dict(body or {})
    try:
        b = CreateItemRequest.model_validate(payload)
    except ValidationError as e:
        return {"success": True, "data": {"ok": False, "blocks": blok_validasi(e), "rencana": None, "payload": payload}}
    bloks, kode = [], (None, None)
    conn = await get_db_connection()
    try:
        tr = conn.transaction(readonly=True)
        await tr.start()
        try:
            tanpa_stok_bila_bukan_barang(b)
            periksa_badan_item(b, "POST /items/preview")
            kode = await _siapkan_buat_barang(conn, ctx["tenant_id"], ctx, b)
        except HTTPException as e:
            bloks.append(blok_dari_galat(e))
        finally:
            await tr.rollback()
    finally:
        await conn.close()
    efek = []
    if b.track_inventory and b.item_type == "goods" and (b.opening_stock or 0) > 0:
        rate = b.opening_stock_rate if b.opening_stock_rate else (b.purchase_price or 0)
        efek.append({"jenis": "saldo_awal_stok", "qty": str(b.opening_stock), "harga": str(rate),
                     "nilai": f"{Decimal(str(b.opening_stock)) * Decimal(str(rate)):.2f}"})
    rencana = {"name": b.name, "item_type": b.item_type, "track_inventory": b.track_inventory, "bisa_dikirim": b.bisa_dikirim,
               "base_unit": b.base_unit, "sales_price": b.sales_price, "item_code": kode[0], "sku": kode[1], "efek": efek}
    return {"success": True, "data": {"ok": not bloks, "blocks": bloks, "rencana": rencana, "payload": payload}}


# =============================================================================
# IMPOR BARANG (BUG-021): Excel + chatbox -> pratinjau per baris -> tulis ATOMIK (services/impor_barang)
# =============================================================================


@router.get("/items/impor/templat")
async def templat_impor_barang(request: Request):
    """Templat .xlsx (lembar Barang + Petunjuk + satuan tenant). Kolom = services/impor_barang.KOLOM."""
    ctx = get_user_context(request)
    conn = await get_db_connection()
    try:
        satuan = await SM.daftar(conn, ctx["tenant_id"])
    finally:
        await conn.close()
    return Response(content=IB.templat_xlsx(satuan),
                    media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": 'attachment; filename="templat-impor-barang.xlsx"'})


@router.post("/items/impor/baca")
async def baca_impor_barang(request: Request, berkas: UploadFile = File(...)):
    """Urai .xlsx -> {baris, pemetaan[{judul_berkas, medan, label}], tak_dipakai, peringatan} (nol DB; BUG-101 judul bebas + kolom tak
    dikenal disebut). FE menampilkan lalu mengirim baris ke /items/impor/pratinjau."""
    get_user_context(request)
    isi = await berkas.read(2 * 1024 * 1024 + 1)
    if len(isi) > 2 * 1024 * 1024:
        raise HTTPException(status_code=400, detail={"code": "BERKAS_TERLALU_BESAR", "message": "Berkas maksimal 2 MB."})
    return {"success": True, "data": IB.baca_xlsx(isi)}


def _baris_impor(body: dict) -> list:
    """Badan {baris:[...]} (Excel/tabel) ATAU {teks:"..."} (chatbox, 1..N baris) -> baris ternormalisasi."""
    body = body or {}
    return body["baris"] if isinstance(body.get("baris"), list) else IB.tafsir_teks(body.get("teks") or "")


@router.post("/items/impor/pratinjau")
async def pratinjau_impor_barang(request: Request, body: dict):
    """Pratinjau impor: penentu per baris SAMA dengan POST /items + aturan batch; transaksi READ ONLY, nol tulis.
    200 {success, data:{ok, blocks, ringkas, baris[{no, ok, blocks, rencana, payload}], payload:{baris}}}."""
    ctx = get_user_context(request)
    baris = _baris_impor(body)
    conn = await get_db_connection()
    try:
        tr = conn.transaction(readonly=True)
        await tr.start()
        try:
            r = await IB.rencana(conn, ctx, baris)
        finally:
            await tr.rollback()
    finally:
        await conn.close()
    return {"success": True, "data": {**r, "payload": {"baris": baris}}}


@router.post("/items/impor")
async def impor_barang(request: Request, body: dict, response: Response = None):
    """Tulis impor ATOMIK: semua baris atau tidak sama sekali (pola Odoo/Xero). Badan = payload pratinjau. X-Idempotency-Key
    opsional (replay = respons asli, bukan batch kedua). Blok -> 422 IMPOR_DITOLAK + blok per baris, nol tulis."""
    from ..services import idem_buat
    ctx = get_user_context(request)
    kunci = idem_buat.kunci_dari(request)
    baris = _baris_impor(body)
    conn = await get_db_connection()
    try:
        async with conn.transaction():
            kp, sd, lama = await idem_buat.mulai_aksi(conn, ctx, kunci, "ITEM_IMPORT", "batch", lambda: body, response)
            if lama is not None:
                return lama
            data = await IB.tulis(conn, ctx, baris, request.state.user.get("user_id"),
                                  request.state.user.get("username") or request.state.user.get("email"))
            resp = {"success": True, "message": f"{data['jumlah']} barang diimpor.", "data": data}
            return await idem_buat.simpan(conn, ctx, kp, sd, "ITEM_IMPORT", resp, None)
    finally:
        await conn.close()


async def tulis_barang_baru(conn, ctx: dict, body: CreateItemRequest, user_id, user_name):
    """Tulis SATU barang di transaksi/koneksi PEMANGGIL (POST /items dan Milkybook v2 buat atomik, 1 Nov 2026): penentu
    _siapkan_buat_barang + INSERT + konversi satuan + saldo awal stok + aktivitas. -> id barang (UUID).
    Pemanggil wajib lebih dulu tanpa_stok_bila_bukan_barang + periksa_badan_item (urutan lama create_item)."""
    tenant_id = ctx["tenant_id"]
    item_code_to_use, sku_to_use = await _siapkan_buat_barang(conn, tenant_id, ctx, body)

    # Start transaction
    async with conn.transaction():
        if not body.item_code:
            # Kode OTOMATIS = nomor terbesar + 1: dua penulis serentak mendapat kode SAMA -> bentrok indeks (500; terukur di salinan 12 Nov).
            # Kunci per tenant di DALAM transaksi lalu hitung ULANG (validasi di atas tetap sebelum transaksi); lepas saat commit.
            await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", f"ITEM_KODE:{tenant_id}")
            item_code_to_use = await _kode_otomatis(conn, tenant_id, body.item_type)
            if not body.sku:
                sku_to_use = item_code_to_use
        # Insert item
        # Parse opening_stock_date if provided
        opening_date = None
        if body.opening_stock_date:
            try:
                opening_date = dateclass.fromisoformat(body.opening_stock_date)
            except (ValueError, TypeError):
                opening_date = None

        insert_query = """
            INSERT INTO products (
                tenant_id, nama_produk, satuan, base_unit, kategori, deskripsi, barcode,
                item_type, track_inventory, is_returnable,
                sales_account, purchase_account, sales_tax, purchase_tax,
                sales_price, purchase_price, harga_jual,
                image_url, reorder_level, preferred_vendor_id,
                sales_account_id, purchase_account_id,
                item_code, sku, inventory_account_id, cogs_account_id,
                costing_method, opening_stock, opening_stock_rate, opening_stock_date,
                for_sales, for_purchases, warehouse_id,
                track_batches, track_expiry, default_expiry_days,
                sales_tax_id, purchase_tax_id, bisa_dikirim,
                created_at, updated_at
            ) VALUES (
                $1, $2, $3, $4, $5, $6, $7,
                $8, $9, $10,
                $11, $12, $13, $14,
                $15, $16, $17,
                $18, $19, $20,
                $21, $22,
                $23, $24, $25, $26,
                $27, $28, $29, $30,
                $31, $32, $33,
                $34, $35, $36,
                $37, $38, $39,
                NOW(), NOW()
            )
            RETURNING id
        """
        item_id = await conn.fetchval(
            insert_query,
            ctx["tenant_id"],
            body.name,
            body.base_unit,  # satuan = base_unit
            body.base_unit,
            body.kategori,
            body.deskripsi,
            body.barcode,
            body.item_type,
            body.track_inventory,
            body.is_returnable,
            body.sales_account,
            body.purchase_account,
            body.sales_tax,
            body.purchase_tax,
            body.sales_price,
            body.purchase_price,
            body.sales_price,  # harga_jual = sales_price for backwards compat
            body.image_url,
            body.reorder_level,
            body.preferred_vendor_id,
            body.sales_account_id,
            body.purchase_account_id,
            item_code_to_use,
            sku_to_use,
            body.inventory_account_id,
            body.cogs_account_id,
            body.costing_method,
            body.opening_stock,
            body.opening_stock_rate,
            opening_date,
            body.for_sales,
            body.for_purchases,
            body.warehouse_id,
            body.track_batches,
            body.track_expiry,
            body.default_expiry_days,
            body.sales_tax_id,
            body.purchase_tax_id,
            # V318: flag non-stok bisa dikirim, SATU INSERT (dulu UPDATE kedua); CHECK DB menolak jasa
            bool(body.bisa_dikirim),
        )

        # Insert unit conversions (goods only)
        if body.item_type == "goods" and body.conversions:
            for conv in body.conversions:
                await conn.execute(
                    """
                    INSERT INTO unit_conversions (
                        tenant_id, product_id, base_unit, conversion_unit, conversion_factor,
                        purchase_price, sales_price, is_active, created_at, updated_at
                    ) VALUES ($1, $2, $3, $4, $5, $6, $7, true, NOW(), NOW())
                    """,
                    tenant_id,
                    item_id,
                    body.base_unit,
                    conv.conversion_unit,
                    conv.conversion_factor,
                    conv.purchase_price,
                    conv.sales_price,
                )

        # Create initial stock entry (if track_inventory)
        if body.track_inventory and body.item_type == "goods":
            # Determine opening stock values
            initial_qty = (
                Decimal(str(body.opening_stock))
                if body.opening_stock
                else Decimal("0")
            )
            initial_rate = (
                Decimal(str(body.opening_stock_rate))
                if body.opening_stock_rate
                else (
                    Decimal(str(body.purchase_price))
                    if body.purchase_price
                    else Decimal("0")
                )
            )
            initial_value = initial_qty * initial_rate

            # Create inventory_ledger OPENING_BALANCE entry if opening stock > 0
            # (Law 16: stock derived from inventory_ledger, no persediaan writes)
            if initial_qty > 0:
                # t10-tanggal-bisnis: cadangan tanggal saldo awal = hari ini
                # menurut zona tenant, bukan date.today() (UTC container:
                # 00-07 WIB memberi KEMARIN).
                ob_date = None
                if body.opening_stock_date:
                    try:
                        ob_date = dateclass.fromisoformat(
                            str(body.opening_stock_date)
                        )
                    except (ValueError, TypeError):
                        ob_date = None
                if ob_date is None:
                    ob_date = await tanggal_dokumen(conn, tenant_id)

                # GUDANG WAJIB, juga untuk saldo awal (11 Sep 2026).
                # Titik ini tak pernah menyebut `warehouse_id` sama sekali,
                # jadi ia menulis NULL SECARA STRUKTURAL -- bukan lewat
                # rantai cadangan yang kebetulan kosong. Barisnya masuk
                # buku, lalu trigger `trg_update_warehouse_stock` (yang
                # mencocokkan `warehouse_id = NEW.warehouse_id`) tak pernah
                # cocok: stok awal terlihat di layar dan ditolak saat
                # dikirim. Baris saldo awal yang SUDAH tercatat tidak
                # disentuh di sini -- itu koreksi data, tiket tersendiri,
                # dan `inventory_ledger` bersifat hanya-tambah (Rule 8).
                ob_warehouse = await conn.fetchval(
                    """
                    SELECT id FROM warehouses
                    WHERE tenant_id = $1 AND is_active = true
                    ORDER BY is_default DESC, created_at ASC
                    LIMIT 1
                    """,
                    ctx["tenant_id"],
                )
                if ob_warehouse is None:
                    raise HTTPException(
                        status_code=409,
                        detail=(
                            "Saldo awal persediaan butuh gudang, dan tenant "
                            "ini belum punya gudang aktif. Buat gudang dulu, "
                            "atau simpan barang tanpa saldo awal."
                        ),
                    )
                # Audit F1 (10 Okt 2026): saldo awal = JURNAL (Dr Persediaan / Cr Modal Saldo Awal) + kartu stok di
                # transaksi ini. Dulu hanya baris kartu -> kartu bernilai, buku besar tidak (Law 1/4/6/16/23).
                await catat_saldo_awal_stok(
                    conn,
                    tenant_id=ctx["tenant_id"],
                    user_id=ctx.get("user_id"),
                    product_id=item_id,
                    product_code=item_code_to_use,
                    product_name=body.name,
                    tanggal=ob_date,
                    qty=initial_qty,
                    rate=initial_rate,
                    warehouse_id=ob_warehouse,
                    inventory_account_id=body.inventory_account_id,
                )

        # Log activity
        await conn.execute(
            """
            INSERT INTO item_activities (item_id, tenant_id, type, description, actor_id, actor_name)
            VALUES ($1, $2, 'created', 'Item dibuat', $3, $4)
            """,
            item_id,
            tenant_id,
            user_id if user_id else None,
            user_name,
        )
    return item_id


@router.post("/items", response_model=CreateItemResponse)
async def create_item(request: Request, body: CreateItemRequest, response: Response = None):
    """
    Create a new item (product or service).

    For goods with track_inventory=true, an initial stock entry will be created.
    Unit conversions are only allowed for goods.
    """
    ctx = get_user_context(request)
    tenant_id = ctx["tenant_id"]
    conn = None
    tanpa_stok_bila_bukan_barang(body)  # 10 Okt: non_inventory/service tak pernah track_inventory
    periksa_badan_item(body, "POST /items")
    from ..services import idem_buat
    _kunci = idem_buat.kunci_dari(request)  # AS-G14: X-Idempotency-Key opsional (pola QUOTE_CREATE)

    try:
        conn = await get_db_connection()

        _tx, _kp, _sd = None, None, None
        if _kunci:  # AS-G14: transaksi LUAR hanya bila ber-kunci -- idem lock + replay SEBELUM penentu (ketuk ganda = replay, bukan 409
            _tx = conn.transaction()  # nama dipakai); galat -> conn.close() di finally = rollback; tulis lama = savepoint di dalamnya.
            await _tx.start()         # Tanpa kunci = perilaku lama (penentu menolak sebelum transaksi apa pun dibuka).
            _kp, _sd, _lama = await idem_buat.mulai(conn, ctx, _kunci, "ITEM_CREATE", body, "products", "item", response)
            if _lama is not None:
                await _tx.rollback()
                return _lama
        item_id = await tulis_barang_baru(conn, ctx, body, request.state.user.get("user_id"),
                                          request.state.user.get("username") or request.state.user.get("email"))

        logger.info(f"Created item {body.name} (id={item_id}) for tenant {tenant_id}")

        _hasil = CreateItemResponse(
            success=True,
            message=f"Item '{body.name}' berhasil ditambahkan",
            data={
                "id": str(item_id),
                "name": body.name,
                "item_type": body.item_type,
                "track_inventory": body.track_inventory,
                "bisa_dikirim": body.bisa_dikirim,
            },
        )
        if _tx is not None:
            _hasil = await idem_buat.simpan(conn, ctx, _kp, _sd, "ITEM_CREATE", _hasil, item_id)
            await _tx.commit()
        return _hasil

    except HTTPException:
        raise
    except Exception as e:
        galat = galat_bentrok_unik(e, body)
        if galat:
            raise galat from e
        logger.error(f"Error creating item: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if conn:
            await conn.close()


# =============================================================================
# UPDATE ITEM
# =============================================================================


async def _item_has_transactions(conn, tenant_id, item_id):
    """True bila barang sudah dipakai di ledger stok / faktur / tagihan non-draft.
    Dipakai untuk memblok perubahan item_type / track_inventory yang merusak valuasi
    persediaan & HPP (E2b). item_id = UUID."""
    return bool(await conn.fetchval(
        """
        SELECT EXISTS(SELECT 1 FROM inventory_ledger
                      WHERE product_id = $1 AND tenant_id = $2)
            OR EXISTS(SELECT 1 FROM sales_invoice_items sii
                      JOIN sales_invoices si ON si.id = sii.invoice_id
                      WHERE sii.item_id = $1 AND si.tenant_id = $2
                        AND si.status NOT IN ('draft', 'void'))
            OR EXISTS(SELECT 1 FROM bill_items bi
                      JOIN bills b ON b.id = bi.bill_id
                      WHERE bi.product_id = $1 AND b.tenant_id = $2
                        AND b.status_v2 NOT IN ('draft', 'void'))
        """,
        item_id, tenant_id,
    ))


async def _item_jebakan_ubah_tipe(conn, tenant_id, item_id):
    """Q-018 (26 Sep 2026): alasan NYATA menolak ubah tipe/pelacakan lewat impor massal, atau None.

    Pemilik SENGAJA menjadikan barang non_inventory (jual+beli dulu) -> tak boleh diblok umum. Jebakan nyata hanya:
    (a) baris faktur terbit yang pendapatannya masih DITUNDA (INV-2609-0009: pindah tipe -> pendapatan terkunci),
    (b) riwayat inventory_ledger (valuasi persediaan/HPP).
    """
    tunda = await conn.fetchval(
        """SELECT string_agg(DISTINCT si.invoice_number, ', ') FROM sales_invoice_items sii
           JOIN sales_invoices si ON si.id = sii.invoice_id
           WHERE sii.item_id = $1 AND si.tenant_id = $2 AND si.status IN ('posted', 'partial', 'paid')
             AND COALESCE(sii.allocated_amount, 0) - COALESCE(sii.recognized_amount, 0) > 0.005""",
        item_id, tenant_id,
    )
    if tunda:
        return f"pendapatan faktur {tunda} masih ditunda — akui dulu lewat aksi Akui pendapatan baris non-stok di faktur itu"
    if await conn.fetchval("SELECT EXISTS(SELECT 1 FROM inventory_ledger WHERE product_id = $1 AND tenant_id = $2)", item_id, tenant_id):
        return "barang punya riwayat ledger stok (valuasi persediaan/HPP)"
    return None


def _lacak_efektif(tipe_baru, lacak_diminta, tipe_lama, lacak_lama):
    """Pelacakan stok yang KONSISTEN dengan tipe: jasa/non_inventory tak pernah dilacak; goods dari tipe tak-dilacak
    = dilacak (bawaan goods) kecuali diminta lain; goods -> goods memakai permintaan/nilai lama."""
    if tipe_baru in ("service", "non_inventory"):
        return False
    if lacak_diminta is not None:
        return bool(lacak_diminta)
    if tipe_lama != "goods":
        return True
    return bool(lacak_lama)


async def _alasan_tolak_ubah_tipe(conn, tenant_id, item_id, tipe_lama, lacak_lama, tipe_baru, lacak_baru):
    """SATU aturan ubah tipe/pelacakan untuk form (PUT) DAN impor massal (27 Sep 2026, putusan pemilik via MASTER).
    Per ARAH (pola Xero/QuickBooks):
      (1) dilacak -> tak dilacak (goods ber-stok -> non_inventory/jasa/goods tanpa lacak): penjaga SEMPIT Q-018 —
          tolak hanya bila pendapatan faktur masih DITUNDA atau ada riwayat inventory_ledger (niat pemilik: barang
          sengaja dijadikan non_inventory).
      (2) tak dilacak -> dilacak (non_inventory/jasa -> goods ber-stok): KETAT E2b — tolak bila sudah ada faktur/
          tagihan terbit atau ledger (penjualan lampau tanpa HPP/valuasi; opsi pemilik: buat barang BARU ber-stok).
      (3) antar tak-dilacak (jasa <-> non_inventory): penjaga sempit yang sama dengan (1) (tak ada stok/HPP yang
          terganggu; pendapatan tertunda tetap dijaga).
    -> alasan (str) atau None. Tenant eksplisit di semua kueri (Law 24)."""
    if tipe_baru == tipe_lama and bool(lacak_baru) == bool(lacak_lama):
        return None
    if lacak_baru and not lacak_lama:
        if await _item_has_transactions(conn, tenant_id, item_id):
            return ("barang sudah punya transaksi (faktur/tagihan/ledger stok) tanpa HPP/valuasi persediaan — "
                    "buat barang BARU ber-stok untuk penjualan berikutnya")
        return None
    return await _item_jebakan_ubah_tipe(conn, tenant_id, item_id)


async def _periksa_ubah(conn, ctx: dict, item_id, body):
    """SEMUA penghalang ubah barang yang dibaca sebelum menulis (ada, nama/barcode ganda, satuan dasar terkunci, ubah tipe/lacak):
    SATU penentu untuk PUT dan pratinjau /edit/preview. Mengembalikan baris lama; menormalkan body.track_inventory."""
    tenant_id = ctx["tenant_id"]
    # Check item exists
    existing = await conn.fetchrow(
        "SELECT id FROM products WHERE id = $1 AND tenant_id = $2",
        str(item_id),
        tenant_id,
    )
    if not existing:
        raise HTTPException(status_code=404, detail="Item not found")

    # Check for duplicate name (if changing)
    if body.name:
        duplicate = await conn.fetchrow(
            f"SELECT {SQL_PEMEGANG} FROM products WHERE tenant_id = $1 AND nama_produk = $2 AND id != $3 AND deleted_at IS NULL",
            ctx["tenant_id"],
            body.name,
            str(item_id),
        )
        if duplicate:
            raise galat_nama_barang_dipakai(duplicate)

    # Check for duplicate barcode (if changing)
    if body.barcode:
        duplicate_barcode = await conn.fetchrow(
            # Sapuan tenant 26 Sep 2026: dulu lintas-tenant -> 409 palsu + oracle keberadaan.
            f"SELECT {SQL_PEMEGANG} FROM products WHERE barcode = $1 AND id != $2 AND tenant_id = $3 "
            "ORDER BY (deleted_at IS NULL) DESC LIMIT 1",
            body.barcode,
            str(item_id),
            tenant_id,
        )
        if duplicate_barcode:
            raise galat_barcode_barang_dipakai(duplicate_barcode)

    # Fetch old values for change tracking
    old_item = await conn.fetchrow(
        """SELECT nama_produk, sales_price, harga_jual, purchase_price,
                  base_unit, satuan, reorder_level, item_type, track_inventory, bisa_dikirim,
                  kategori, deskripsi, barcode, is_returnable,
                  sales_tax, purchase_tax, image_url,
                  inventory_account_id, cogs_account_id
           FROM products WHERE id = $1 AND tenant_id = $2""",
        str(item_id),
        tenant_id,
    )

    # Satuan dasar terkunci sesudah ada riwayat (putusan pemilik LANGSUNG 5 Nov; penentu SATU satuan_dasar)
    from ..services import satuan_dasar as _SDK
    if body.base_unit is not None and old_item:
        await _SDK.tolak_ubah_dasar(conn, tenant_id, str(item_id), old_item["base_unit"] or old_item["satuan"], body.base_unit)

    # Ubah tipe/pelacakan: SATU aturan per arah, sama dengan impor massal (_alasan_tolak_ubah_tipe).
    # Pelacakan dinormalkan ke tipe (jasa/non_inventory tak pernah dilacak).
    _tipe_lama = old_item["item_type"] if old_item else None
    _lacak_lama = bool(old_item["track_inventory"]) if old_item else False
    _tipe_baru = body.item_type if body.item_type is not None else _tipe_lama
    _lacak_baru = _lacak_efektif(_tipe_baru, body.track_inventory, _tipe_lama, _lacak_lama)
    if old_item:
        _alasan = await _alasan_tolak_ubah_tipe(
            conn, tenant_id, item_id, _tipe_lama, _lacak_lama, _tipe_baru, _lacak_baru
        )
        if _alasan:
            raise HTTPException(
                status_code=400,
                detail=f"Tipe barang/jasa atau pelacakan stok tidak bisa diubah: {_alasan}.",
            )
        if _lacak_baru != _lacak_lama or body.track_inventory is not None:
            body.track_inventory = _lacak_baru
    return old_item


async def _periksa_konversi(conn, ctx: dict, item_id, body) -> None:
    """P1 (putusan pemilik 5 Nov): konversi aktif yang hilang / berubah faktor DAN sudah terpakai transaksi -> 409. PUT + pratinjau."""
    if body.conversions is None:
        return
    from ..services import satuan_dasar as _SDK
    await _SDK.tolak_ubah_konversi(conn, ctx["tenant_id"], str(item_id), await conn.fetch(
        """SELECT conversion_unit, conversion_factor FROM unit_conversions
           WHERE product_id = $1 AND tenant_id = $2 AND COALESCE(is_active, true)""", str(item_id), ctx["tenant_id"]),
        [(c.conversion_unit, c.conversion_factor) for c in body.conversions if c.is_active is not False])


@router.put("/items/{item_id}", response_model=UpdateItemResponse)
async def update_item(request: Request, item_id: UUID, body: UpdateItemRequest, response: Response = None):
    """Update an existing item."""
    ctx = get_user_context(request)
    tenant_id = ctx["tenant_id"]
    conn = None
    periksa_badan_item(body, "PUT /items/{id}")

    try:
        conn = await get_db_connection()

        old_item = await _periksa_ubah(conn, ctx, item_id, body)

        async with conn.transaction():
            _kp, _sd, _lama = await idem_buat.mulai_aksi(conn, ctx, idem_buat.kunci_dari(request), "ITEM_UPDATE", str(item_id),
                                                         lambda: body.model_dump(mode="json"), response)
            if _lama is not None:
                return _lama
            # Build update query dynamically
            updates = []
            params = []
            param_idx = 1

            field_mappings = {
                "name": "nama_produk",
                "item_type": "item_type",
                "track_inventory": "track_inventory",
                "bisa_dikirim": "bisa_dikirim",
                "base_unit": "base_unit",
                "barcode": "barcode",
                "kategori": "kategori",
                "deskripsi": "deskripsi",
                "is_returnable": "is_returnable",
                "sales_account": "sales_account",
                "purchase_account": "purchase_account",
                "sales_account_id": "sales_account_id",
                "purchase_account_id": "purchase_account_id",
                "sales_tax": "sales_tax",
                "purchase_tax": "purchase_tax",
                "sales_tax_id": "sales_tax_id",
                "purchase_tax_id": "purchase_tax_id",
                "sales_price": "sales_price",
                "purchase_price": "purchase_price",
                "image_url": "image_url",
                "reorder_level": "reorder_level",
                "preferred_vendor_id": "preferred_vendor_id",
                # New fields for QB/Xero/Zoho compatibility
                "item_code": "item_code",
                "sku": "sku",
                "inventory_account_id": "inventory_account_id",
                "cogs_account_id": "cogs_account_id",
                "costing_method": "costing_method",
                "opening_stock": "opening_stock",
                "opening_stock_rate": "opening_stock_rate",
                "opening_stock_date": "opening_stock_date",
                "for_sales": "for_sales",
                "for_purchases": "for_purchases",
                "warehouse_id": "warehouse_id",
                "track_batches": "track_batches",
                "track_expiry": "track_expiry",
                "default_expiry_days": "default_expiry_days",
            }

            body_dict = body.model_dump(exclude_unset=True, exclude={"conversions"})
            # V318: jasa tak pernah bisa dikirim. Mengubah tipe ke jasa MEMATIKAN flag; menyalakan flag pada jasa = 422.
            _eff_tipe = body_dict.get("item_type", old_item["item_type"] if old_item else None)
            if _eff_tipe == "service":
                if body_dict.get("bisa_dikirim"):
                    raise HTTPException(status_code=422, detail="Jasa tidak bisa ditandai 'bisa dikirim'")
                if old_item and old_item["bisa_dikirim"]:
                    body_dict["bisa_dikirim"] = False
            if "bisa_dikirim" in body_dict and body_dict["bisa_dikirim"] is None:
                body_dict.pop("bisa_dikirim")
            # Pajak item: id = otoritas, nama teks diisi dari tax_codes (W-20261013-ITEMFIELD; dulu id dibuang diam).
            if body_dict.get("sales_tax_id") is not None:
                body_dict["sales_tax"] = await nama_pajak_item(conn, tenant_id, body_dict["sales_tax_id"], "sales_tax_id", "output")
            if body_dict.get("purchase_tax_id") is not None:
                body_dict["purchase_tax"] = await nama_pajak_item(conn, tenant_id, body_dict["purchase_tax_id"], "purchase_tax_id", "input")

            # Auto-default inventory/COGS accounts for stockable goods. Compute the
            # effective type/flags + account ids (post-update if changed, else current
            # row) and fill any NULL via the tenant's role mapping so the COGS/restock
            # path no longer depends on an implicit silent fallback. Stockable goods only.
            eff_item_type = body_dict.get(
                "item_type", old_item["item_type"] if old_item else None
            )
            eff_track_inventory = body_dict.get(
                "track_inventory",
                old_item["track_inventory"] if old_item else False,
            )
            eff_inv_acct = body_dict.get(
                "inventory_account_id",
                str(old_item["inventory_account_id"])
                if old_item and old_item["inventory_account_id"]
                else None,
            )
            eff_cogs_acct = body_dict.get(
                "cogs_account_id",
                str(old_item["cogs_account_id"])
                if old_item and old_item["cogs_account_id"]
                else None,
            )
            new_inv_acct, new_cogs_acct = await _auto_default_product_accounts(
                conn,
                tenant_id,
                eff_item_type,
                eff_track_inventory,
                eff_inv_acct,
                eff_cogs_acct,
            )
            if new_inv_acct and new_inv_acct != eff_inv_acct:
                body_dict["inventory_account_id"] = new_inv_acct
            if new_cogs_acct and new_cogs_acct != eff_cogs_acct:
                body_dict["cogs_account_id"] = new_cogs_acct

            for field, db_field in field_mappings.items():
                if field in body_dict:
                    updates.append(f"{db_field} = ${param_idx}")
                    params.append(body_dict[field])
                    param_idx += 1

                    # Keep harga_jual in sync with sales_price
                    if field == "sales_price":
                        updates.append(f"harga_jual = ${param_idx}")
                        params.append(body_dict[field])
                        param_idx += 1

                    # Keep satuan in sync with base_unit
                    if field == "base_unit":
                        updates.append(f"satuan = ${param_idx}")
                        params.append(body_dict[field])
                        param_idx += 1

            if updates:
                updates.append("updated_at = NOW()")
                params.extend([str(item_id), tenant_id])

                update_query = f"""
                    UPDATE products SET {', '.join(updates)}
                    WHERE id = ${param_idx} AND tenant_id = ${param_idx + 1}
                """
                await conn.execute(update_query, *params)

            # Update conversions if provided
            if body.conversions is not None:
                # P1 (putusan pemilik 5 Nov): konversi aktif yang hilang / berubah faktor DAN sudah terpakai transaksi -> 409
                await _periksa_konversi(conn, ctx, item_id, body)
                # Deactivate all existing conversions
                await conn.execute(
                    "UPDATE unit_conversions SET is_active = false WHERE product_id = $1 AND tenant_id = $2",
                    str(item_id),
                    ctx["tenant_id"],
                )

                # Insert/update new conversions
                base_unit = (
                    body.base_unit
                    or (
                        await conn.fetchval(
                            "SELECT base_unit FROM products WHERE id = $1", str(item_id)
                        )
                    )
                    or "pcs"
                )

                for conv in body.conversions:
                    if conv.id:
                        # Update existing
                        await conn.execute(
                            """
                            UPDATE unit_conversions SET
                                conversion_unit = $1, conversion_factor = $2,
                                purchase_price = $3, sales_price = $4,
                                is_active = $5, updated_at = NOW()
                            WHERE id = $6
                            """,
                            conv.conversion_unit,
                            conv.conversion_factor,
                            conv.purchase_price,
                            conv.sales_price,
                            conv.is_active,
                            str(conv.id),
                        )
                    else:
                        # Insert new
                        await conn.execute(
                            """
                            INSERT INTO unit_conversions (
                                tenant_id, product_id, base_unit, conversion_unit, conversion_factor,
                                purchase_price, sales_price, is_active, created_at, updated_at
                            ) VALUES ($1, $2, $3, $4, $5, $6, $7, true, NOW(), NOW())
                            """,
                            tenant_id,
                            str(item_id),
                            base_unit,
                            conv.conversion_unit,
                            conv.conversion_factor,
                            conv.purchase_price,
                            conv.sales_price,
                        )

            # Log activity
            body_dict_for_log = body.model_dump(
                exclude_unset=True, exclude={"conversions"}
            )
            if body_dict_for_log:
                change_parts = []
                field_labels = {
                    "name": ("Nama", "nama_produk"),
                    "sales_price": ("Harga jual", "sales_price", True),
                    "purchase_price": ("Harga beli", "purchase_price", True),
                    "base_unit": ("Satuan", "base_unit"),
                    "reorder_level": ("Titik reorder", "reorder_level"),
                    "kategori": ("Kategori", "kategori"),
                    "deskripsi": ("Deskripsi", "deskripsi"),
                    "barcode": ("Barcode", "barcode"),
                    "sales_tax": ("Pajak jual", "sales_tax"),
                    "purchase_tax": ("Pajak beli", "purchase_tax"),
                }

                only_price = True

                for field, meta in field_labels.items():
                    if field in body_dict_for_log:
                        label = meta[0]
                        db_col = meta[1]
                        is_price = len(meta) > 2 and meta[2]
                        old_val = old_item.get(db_col) if old_item else None
                        new_val = body_dict_for_log[field]

                        # Normalize values for comparison (handle numeric precision)
                        def normalize_val(v):
                            if v is None:
                                return None
                            if isinstance(v, (int, float, Decimal)):
                                return Decimal(str(v))
                            try:
                                return Decimal(str(v))
                            except (ValueError, TypeError, ArithmeticError):
                                return str(v)

                        old_norm = normalize_val(old_val)
                        new_norm = normalize_val(new_val)

                        if old_norm != new_norm and not (
                            old_norm is None and new_norm is None
                        ):
                            if is_price:
                                old_display = (
                                    f"Rp {int(old_val):,}".replace(",", ".")
                                    if old_val
                                    else "0"
                                )
                                new_display = (
                                    f"Rp {int(new_val):,}".replace(",", ".")
                                    if new_val
                                    else "0"
                                )
                            else:
                                old_display = str(old_val) if old_val else "-"
                                new_display = str(new_val) if new_val else "-"
                            change_parts.append(
                                f"{label}: {old_display} \u2192 {new_display}"
                            )
                            if field not in ("sales_price", "purchase_price"):
                                only_price = False

                # Determine activity type
                if (
                    only_price
                    and change_parts
                    and all("Harga" in p for p in change_parts)
                ):
                    activity_type = "price_changed"
                    activity_desc = "Harga diubah"
                else:
                    activity_type = "updated"
                    activity_desc = "Item diperbarui"

                details = "\n".join(change_parts) if change_parts else None
                user_id = request.state.user.get("user_id")
                user_name = request.state.user.get(
                    "username"
                ) or request.state.user.get("email")

                if change_parts:  # Only log if there were actual changes
                    await conn.execute(
                        """
                        INSERT INTO item_activities (item_id, tenant_id, type, description, details, actor_id, actor_name)
                        VALUES ($1, $2, $3, $4, $5, $6, $7)
                        """,
                        str(item_id),
                        tenant_id,
                        activity_type,
                        activity_desc,
                        details,
                        user_id if user_id else None,
                        user_name,
                    )

            await idem_buat.simpan(conn, ctx, _kp, _sd, "ITEM_UPDATE", {"success": True, "message": "Item berhasil diperbarui",
                                                                          "data": {"id": str(item_id)}}, item_id)

        logger.info(f"Updated item {item_id} for tenant {tenant_id}")

        return UpdateItemResponse(
            success=True, message="Item berhasil diperbarui", data={"id": str(item_id)}
        )

    except HTTPException:
        raise
    except Exception as e:
        galat = galat_bentrok_unik(e, body)
        if galat:
            raise galat from e
        logger.error(f"Error updating item: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if conn:
            await conn.close()


# =============================================================================
# DELETE ITEM
# =============================================================================


@router.post("/items/{item_id}/edit/preview")
async def preview_ubah_barang(request: Request, item_id: UUID, body: dict):
    """Pratinjau UBAH barang: penghalang dari penentu SAMA dgn PUT (_periksa_ubah + _periksa_konversi) lalu daftar perubahan {medan, dari, ke}.
    READ ONLY; galat skema -> blok DATA_TIDAK_SAH per medan (bukan 422)."""
    from pydantic import ValidationError
    from .customers import blok_dari_galat, blok_validasi
    ctx = get_user_context(request)
    payload = dict(body or {})
    try:
        b = UpdateItemRequest.model_validate(payload)
    except ValidationError as e:
        return {"success": True, "data": {"ok": False, "blocks": blok_validasi(e), "peringatan": [], "rencana": None, "payload": payload}}

    async def f(conn):
        try:
            periksa_badan_item(b, "POST /items/{id}/edit/preview")
            lama = await _periksa_ubah(conn, ctx, item_id, b)
            await _periksa_konversi(conn, ctx, item_id, b)
        except HTTPException as e:
            if e.status_code == 404:
                return BA.rencana_ubah(None, {})
            return {"ok": False, "blocks": [blok_dari_galat(e)], "peringatan": [], "rencana": None}
        return BA.rencana_ubah(dict(lama), b.model_dump(exclude_unset=True))
    r = await _baca_saja(f)
    return {"success": True, "data": {**r, "payload": payload}}


async def _baca_saja(fungsi):
    """Transaksi READ ONLY untuk pratinjau (nol tulis, rollback)."""
    conn = await get_db_connection()
    try:
        tr = conn.transaction(readonly=True)
        await tr.start()
        try:
            return await fungsi(conn)
        finally:
            await tr.rollback()
    finally:
        await conn.close()


def _ke_aktif(body: dict) -> bool:
    v = (body or {}).get("is_active")
    if not isinstance(v, bool):
        raise HTTPException(status_code=422, detail={"code": "DATA_TIDAK_SAH", "message": "is_active wajib true atau false.", "field": "is_active"})
    return v


@router.post("/items/{item_id}/status/preview")
async def preview_status_barang(request: Request, item_id: UUID, body: dict):
    """Pratinjau aktifkan/nonaktifkan SATU barang: nonaktif ber-stok/pesanan terbuka = peringatan (bukan blok), tanpa jurnal."""
    ctx = get_user_context(request)
    ke = _ke_aktif(body)

    async def f(conn):
        return await BA.ukur_rencana_status(conn, ctx["tenant_id"], str(item_id), ke)
    return {"success": True, "data": await _baca_saja(f)}


@router.post("/items/{item_id}/delete/preview")
async def preview_hapus_barang(request: Request, item_id: UUID):
    """Pratinjau hapus barang: rincian jejak per sumber (daftar SAMA dgn penulis), saran Nonaktifkan bila berjejak."""
    ctx = get_user_context(request)

    async def f(conn):
        b = await BA.ukur_barang(conn, ctx["tenant_id"], str(item_id))
        return BA.rencana_hapus(b, await BA.ukur_jejak(conn, str(item_id)) if b else [])
    return {"success": True, "data": await _baca_saja(f)}


@router.post("/items/{item_id}/duplicate/preview")
async def preview_duplikat_barang(request: Request, item_id: UUID):
    """Pratinjau duplikat: nama + kode baru (penentu sama dgn penulis), yang disalin (data, konversi, harga bertingkat) dan yang tidak."""
    ctx = get_user_context(request)

    async def f(conn):
        b = await BA.ukur_barang(conn, ctx["tenant_id"], str(item_id))
        if not b:
            return BA.rencana_duplikat(None, "", None, 0, 0)
        t = ctx["tenant_id"]
        nk = await conn.fetchval("SELECT count(*) FROM unit_conversions WHERE product_id = $1::uuid AND tenant_id = $2 AND is_active = true", str(item_id), t)
        nh = await conn.fetchval("""SELECT count(*) FROM price_list_items pli JOIN price_lists pl ON pl.id = pli.price_list_id
                                   WHERE pli.item_id = $1::uuid AND pl.tenant_id = $2""", str(item_id), t)
        return BA.rencana_duplikat(b, await _nama_salinan(conn, t, b["nama_produk"]), await _kode_otomatis(conn, t, b.get("item_type") or "goods"), nk, nh)
    return {"success": True, "data": await _baca_saja(f)}


@router.post("/items/categories/delete/preview")
async def preview_hapus_kategori(request: Request, body: dict):
    """Pratinjau hapus kategori: terpakai barang aktif -> blok KATEGORI_DIPAKAI + jumlah (putusan 13 Nov, ikut Accurate)."""
    ctx = get_user_context(request)
    nama = str((body or {}).get("name") or "").strip()
    if not nama:
        raise HTTPException(status_code=422, detail={"code": "DATA_TIDAK_SAH", "message": "name wajib.", "field": "name"})

    async def f(conn):
        ada, n = await BA.ukur_kategori(conn, ctx["tenant_id"], nama)
        return BA.rencana_kategori_hapus(nama, n, ada)
    return {"success": True, "data": await _baca_saja(f)}


@router.delete("/items/categories")
async def delete_category(request: Request, name: str = Query(..., min_length=1, max_length=100, description="Nama kategori (boleh mengandung / dsb)")):
    """Hapus kategori dari registry. HANYA bila 0 item AKTIF memakainya (selain itu 409).

    products.kategori (string bebas) TAK disentuh — item lama tetap membawa labelnya; yang
    dihapus hanya baris registry. Pencocokan case-insensitive.
    """
    ctx = get_user_context(request)
    tenant_id = ctx["tenant_id"]
    conn = None
    try:
        conn = await get_db_connection()
        async with conn.transaction():
            async def kerja():
                ada, n = await BA.ukur_kategori(conn, tenant_id, name)   # penentu SAMA dgn pratinjau (barang AKTIF berkategori itu)
                if n > 0:
                    raise HTTPException(status_code=409, detail=f"Kategori dipakai {n} item")
                deleted = await conn.fetchval(
                    "DELETE FROM item_categories WHERE tenant_id = $1 AND lower(name) = lower($2) RETURNING id",
                    tenant_id,
                    name,
                )
                if deleted is None:
                    raise HTTPException(status_code=404, detail="Kategori tidak ditemukan")
                return {"success": True, "message": f"Kategori '{name}' dihapus"}
            return await BA.idem_aksi(conn, ctx, request, "ITEM_KATEGORI_HAPUS", name.lower(), {"hapus": name.lower()}, kerja)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting category: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if conn:
            await conn.close()


@router.delete("/items/{item_id}", response_model=DeleteItemResponse)
async def delete_item(request: Request, item_id: UUID):
    """Delete an item. Hard-deletes if no ledger footprint; else raises 400. X-Idempotency-Key opsional; satu baris audit ITEM_DELETED."""
    from ..services.smart_delete import smart_delete

    ctx = get_user_context(request)
    tenant_id = ctx["tenant_id"]
    conn = None
    try:
        conn = await get_db_connection()
        async with conn.transaction():
            async def kerja():
                b = await BA.ukur_barang(conn, tenant_id, str(item_id))
                result = await smart_delete(
                    conn,
                    tenant_id=tenant_id,
                    entity_id=str(item_id),
                    table="products",
                    name_column="nama_produk",
                    ledger_checks=BA.JEJAK_BARANG,
                    cascade_tables=[
                        ("item_pricing", "product_id"),
                        ("unit_conversions", "product_id"),
                    ],
                    has_footprint_error="Item '{name}' tidak bisa dihapus karena masih memiliki transaksi. Gunakan fitur Nonaktifkan.",
                    id_cast="::uuid",
                    extra_exists_filter="AND deleted_at IS NULL",
                )
                if b:
                    await BA.catat_hapus(conn, tenant_id, str(item_id), b["nama_produk"], b.get("item_code"), ctx.get("user_id"))
                return {"success": True, "message": result["message"]}
            r = await BA.idem_aksi(conn, ctx, request, "ITEM_DELETE", str(item_id), {"hapus": True}, kerja)
        return DeleteItemResponse(success=True, message=r["message"])
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting item: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if conn:
            await conn.close()


# =============================================================================
# UNIT CONVERSIONS (all products)
# =============================================================================


@router.get("/items/conversions")
async def list_all_conversions(request: Request):
    """List all unit conversions for the tenant — for Satuan desktop page."""
    ctx = get_user_context(request)
    _tenant_id = ctx["tenant_id"]  # noqa: F841
    pool = await get_pool()
    async with pool.acquire() as conn:
        try:
            rows = await conn.fetch(
                """
                SELECT uc.id, uc.product_id,
                       COALESCE(p.nama_produk, '') AS product_name,
                       COALESCE(p.sku, '') AS product_code,
                       uc.base_unit, uc.conversion_unit, uc.conversion_factor,
                       uc.sales_price, uc.purchase_price
                FROM unit_conversions uc
                JOIN products p ON p.id = uc.product_id AND p.tenant_id = uc.tenant_id
                WHERE uc.tenant_id = $1 AND uc.is_active = true
                ORDER BY product_name ASC, uc.conversion_factor ASC
            """,
                ctx["tenant_id"],
            )

            conversions = [
                {
                    "id": str(r["id"]),
                    "product_id": str(r["product_id"]),
                    "product_name": r["product_name"],
                    "product_code": r["product_code"],
                    "base_unit": r["base_unit"],
                    "conversion_unit": r["conversion_unit"],
                    "conversion_factor": SD_faktor_json(r["conversion_factor"]),
                    "sales_price": float(r["sales_price"])
                    if r["sales_price"]
                    else None,
                    "purchase_price": float(r["purchase_price"])
                    if r["purchase_price"]
                    else None,
                }
                for r in rows
            ]

            return {"success": True, "conversions": conversions}
        except Exception as e:
            logger.error(f"Error listing all conversions: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail="Gagal memuat konversi")


# =============================================================================
# UNITS
# =============================================================================


@router.get("/items/units", response_model=UnitListResponse)
async def list_units(request: Request):
    """Satuan bawaan + kustom tenant (SATU sumber dengan /api/units/dropdown: services/satuan_master.daftar)."""
    tenant_id = get_tenant_id(request)
    conn = None

    try:
        conn = await get_db_connection()
        semua = await SM.daftar(conn, tenant_id)
        return UnitListResponse(
            success=True,
            default_units=DEFAULT_UNITS,
            custom_units=[u["abbreviation"] for u in semua if not u["bawaan"]],
        )

    except Exception as e:
        logger.error(f"Error listing units: {e}")
        raise HTTPException(status_code=500, detail="Gagal memuat satuan")
    finally:
        if conn:
            await conn.close()


@router.post("/items/units", response_model=CreateUnitResponse)
async def create_unit(request: Request, body: CreateUnitRequest):
    """Buat satuan kustom -> product_units (BUG-014: dulu membalas 200 tanpa menyimpan apa pun)."""
    tenant_id = get_tenant_id(request)
    conn = await get_db_connection()
    try:
        u = await SM.buat(conn, tenant_id, body.name, body.abbreviation)
    finally:
        await conn.close()
    return CreateUnitResponse(success=True, message=f"Satuan '{u['abbreviation']}' tersimpan", unit=u["abbreviation"])


# =============================================================================
# ACCOUNTS & TAX OPTIONS
# =============================================================================


@router.get("/items/accounts", response_model=AccountsResponse)
async def list_accounts(request: Request):
    """Get list of accounts for sales and purchase dropdowns."""
    # For now, return static list
    # TODO: Integrate with CoA when available
    sales = [
        AccountOption(value=acc, label=acc, type="income") for acc in SALES_ACCOUNTS
    ]
    purchases = [
        AccountOption(
            value=acc,
            label=acc,
            type="expense" if acc != "Cost of Goods Sold" else "cogs",
        )
        for acc in PURCHASE_ACCOUNTS
    ]

    return AccountsResponse(
        success=True, sales_accounts=sales, purchase_accounts=purchases
    )


# =============================================================================
# COA-BASED ACCOUNTS (New endpoints querying Chart of Accounts)
# =============================================================================


@router.get("/items/accounts/sales", response_model=CoaAccountsResponse)
async def list_sales_accounts(request: Request):
    """
    Get list of INCOME accounts from Chart of Accounts for sales.

    Returns actual CoA accounts instead of hardcoded list.
    Use these for item sales_account_id selection.
    """
    tenant_id = get_tenant_id(request)
    conn = None

    try:
        conn = await get_db_connection()

        query = f"""
            SELECT id, account_code, name, account_type
            FROM chart_of_accounts
            WHERE tenant_id = $1
              AND account_type IN ('REVENUE', 'OTHER_INCOME')
              AND is_active = true
            ORDER BY account_code ASC
        """
        rows = await conn.fetch(query, tenant_id)

        accounts = [
            CoaAccountOption(
                id=str(row["id"]),
                code=row["account_code"],
                name=row["name"],
                account_type=row["account_type"],
            )
            for row in rows
        ]

        return CoaAccountsResponse(success=True, accounts=accounts)

    except Exception as e:
        logger.error(f"Error listing sales accounts: {e}")
        raise HTTPException(status_code=500, detail="Failed to list sales accounts")
    finally:
        if conn:
            await conn.close()


@router.get("/items/accounts/purchase", response_model=CoaAccountsResponse)
async def list_purchase_accounts(request: Request):
    """
    Get list of EXPENSE accounts from Chart of Accounts for purchases.

    Returns actual CoA accounts instead of hardcoded list.
    Use these for item purchase_account_id selection.
    """
    tenant_id = get_tenant_id(request)
    conn = None

    try:
        conn = await get_db_connection()

        query = """
            SELECT id, account_code, name, account_type
            FROM chart_of_accounts
            WHERE tenant_id = $1
              AND account_type = 'EXPENSE'
              AND is_active = true
            ORDER BY account_code ASC
        """
        rows = await conn.fetch(query, tenant_id)

        accounts = [
            CoaAccountOption(
                id=str(row["id"]),
                code=row["account_code"],
                name=row["name"],
                account_type=row["account_type"],
            )
            for row in rows
        ]

        return CoaAccountsResponse(success=True, accounts=accounts)

    except Exception as e:
        logger.error(f"Error listing purchase accounts: {e}")
        raise HTTPException(status_code=500, detail="Failed to list purchase accounts")
    finally:
        if conn:
            await conn.close()


@router.get("/items/accounts/inventory", response_model=CoaAccountsResponse)
async def list_inventory_accounts(request: Request):
    """
    Get list of ASSET accounts suitable for inventory from Chart of Accounts.

    Filters for accounts with 'persediaan' or 'inventory' in name,
    or accounts starting with code '1-104' (inventory assets).
    """
    tenant_id = get_tenant_id(request)
    conn = None

    try:
        conn = await get_db_connection()

        query = """
            SELECT id, account_code, name, account_type
            FROM chart_of_accounts
            WHERE tenant_id = $1
              AND account_type = 'ASSET'
              AND is_active = true
              AND (
                  name ILIKE '%persediaan%'
                  OR name ILIKE '%inventory%'

              )
            ORDER BY account_code ASC
        """
        rows = await conn.fetch(query, tenant_id)

        accounts = [
            CoaAccountOption(
                id=str(row["id"]),
                code=row["account_code"],
                name=row["name"],
                account_type=row["account_type"],
            )
            for row in rows
        ]

        return CoaAccountsResponse(success=True, accounts=accounts)

    except Exception as e:
        logger.error(f"Error listing inventory accounts: {e}")
        raise HTTPException(status_code=500, detail="Failed to list inventory accounts")
    finally:
        if conn:
            await conn.close()


@router.get("/items/taxes", response_model=TaxOptionsResponse)
async def list_taxes(request: Request):
    """Get list of tax options for goods and services."""
    goods_taxes = [
        TaxOption(value="", label="Tidak Ada", rate=0),
        TaxOption(value="PPN_11", label="PPN 11%", rate=11),
        TaxOption(value="PPN_12", label="PPN 12%", rate=12),
    ]

    service_taxes = [
        TaxOption(value="", label="Tidak Ada", rate=0),
        TaxOption(value="PPN_11", label="PPN 11%", rate=11),
        TaxOption(value="PPN_12", label="PPN 12%", rate=12),
        TaxOption(value="PPH_23_2", label="PPh 23 - 2% (Jasa)", rate=2),
        TaxOption(value="PPH_23_15", label="PPh 23 - 15% (Dividen/Royalti)", rate=15),
    ]

    return TaxOptionsResponse(
        success=True, goods_taxes=goods_taxes, service_taxes=service_taxes
    )


# =============================================================================
# SUMMARY
# =============================================================================


@router.get("/items/summary", response_model=ItemsSummaryResponse)
async def get_items_summary(request: Request, search: Optional[str] = Query(None)):
    """Ringkasan barang: kunci aksi {k}_count/_value dengan predikat + pencarian SAMA dengan GET /items?aksi=k (paritas per kunci).
    tanpa_harga_jual BUKAN partisi stok: barang bisa ada di dua pill. _value = Σ nilai stok (ledger) barang dilacak di kunci itu."""
    tenant_id = get_tenant_id(request)
    conn = None

    try:
        conn = await get_db_connection()
        cari_sql, cari_params = _syarat_cari(search, 2)
        kunci = ",\n".join(
            f"COUNT(*) FILTER (WHERE {BD.sql_aksi(k)}) AS {k}_count, "
            f"COALESCE(SUM(per.total_nilai) FILTER (WHERE {BD.sql_aksi(k)} AND p.item_type = 'goods' AND p.track_inventory = true), 0) AS {k}_value"
            for k in BD.SEMUA_KUNCI)
        summary_query = f"""
            SELECT
                COUNT(*) as total,
                COUNT(*) FILTER (WHERE p.item_type = 'goods') as goods_count,
                COUNT(*) FILTER (WHERE p.item_type = 'service') as service_count,
                COUNT(*) FILTER (WHERE p.track_inventory = true) as tracked_count,
                COUNT(*) FILTER (WHERE {BD.sql_ada_stok()}) as in_stock_count,
                COUNT(*) FILTER (WHERE {BD.sql_aksi("di_bawah_titik_pesan")}) as low_stock_count,
                COUNT(*) FILTER (WHERE {BD.sql_aksi("stok_habis")}) as out_of_stock_count,
                {kunci},
                {BD.SQL_NILAI_STOK} as inventory_value
            FROM products p
            {BD.SQL_PER_STOK}
            WHERE p.tenant_id = $1 AND p.deleted_at IS NULL {cari_sql}
        """
        row = await conn.fetchrow(summary_query, tenant_id, *cari_params)

        data = {
            "total": row["total"] or 0,
            "goods_count": row["goods_count"] or 0,
            "service_count": row["service_count"] or 0,
            "tracked_count": row["tracked_count"] or 0,
            "in_stock_count": row["in_stock_count"] or 0,
            "low_stock_count": row["low_stock_count"] or 0,
            "out_of_stock_count": row["out_of_stock_count"] or 0,
            "inventory_value": _rp(row["inventory_value"]),  # BUG-034 (sama dengan stats.inventoryValue)
        }
        for k in BD.SEMUA_KUNCI:
            data[f"{k}_count"] = row[f"{k}_count"] or 0
            data[f"{k}_value"] = _rp(row[f"{k}_value"])
        return ItemsSummaryResponse(success=True, data=data)

    except Exception as e:
        logger.error(f"Error getting summary: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if conn:
            await conn.close()


# =============================================================================
# STATS
# =============================================================================


# BUG-092: hitungan stok = barang AKTIF saja; di_bawah_reorder (kartu STOK RENDAH) hanya barang ber-titik pesan
# ulang (reorder_level > 0), pola NetSuite/SAP; aktif/nonaktif = Σ server, tak bergantung filter daftar.
STATS_SQL = f"""
    SELECT
        COUNT(*) as total_items,
        COUNT(*) FILTER (WHERE p.item_type = 'goods') as total_goods,
        COUNT(*) FILTER (WHERE p.item_type = 'service') as total_services,
        COUNT(*) FILTER (WHERE p.item_type = 'non_inventory') as total_non_inventory,
        COUNT(*) FILTER (WHERE {BD.sql_ada_stok()}) as in_stock,
        COUNT(*) FILTER (WHERE {BD.sql_aksi("di_bawah_titik_pesan")}) as low_stock,
        COUNT(*) FILTER (WHERE {BD.sql_aksi("stok_habis")}) as out_of_stock,
        COUNT(*) FILTER (WHERE {BD.sql_di_bawah_reorder()}) as di_bawah_reorder,
        COUNT(*) FILTER (WHERE p.status = 'active') as aktif,
        COUNT(*) FILTER (WHERE p.status <> 'active') as nonaktif,
        {BD.SQL_NILAI_STOK} as inventory_value
    FROM products p
    {BD.SQL_PER_STOK}
    WHERE p.tenant_id = $1 AND p.deleted_at IS NULL
"""


@router.get("/items/stats", response_model=ItemsStatsResponse)
async def get_items_stats(request: Request):
    """
    Get aggregate item statistics.

    Stock counts are only for items with item_type='goods' AND track_inventory=true.
    - inStock: current_stock > reorder_level
    - lowStock: current_stock > 0 AND current_stock <= reorder_level
    - outOfStock: current_stock = 0 OR current_stock IS NULL
    """
    tenant_id = get_tenant_id(request)
    conn = None

    try:
        conn = await get_db_connection()

        row = await conn.fetchrow(STATS_SQL, tenant_id)

        return ItemsStatsResponse(
            totalItems=row["total_items"] or 0,
            totalGoods=row["total_goods"] or 0,
            totalServices=row["total_services"] or 0,
            totalNonInventory=row["total_non_inventory"] or 0,
            stock=ItemsStatsStockResponse(
                inStock=row["in_stock"] or 0,
                lowStock=row["low_stock"] or 0,
                outOfStock=row["out_of_stock"] or 0,
                belowReorder=row["di_bawah_reorder"] or 0,
            ),
            activeItems=row["aktif"] or 0,
            inactiveItems=row["nonaktif"] or 0,
            inventoryValue=_rp(row["inventory_value"]),  # BUG-034: Σ server semua barang, bukan Σ halaman termuat
        )

    except Exception as e:
        logger.error(f"Error getting item stats: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if conn:
            await conn.close()


# =============================================================================
# CATEGORIES
# =============================================================================


@router.get("/items/categories", response_model=CategoryListResponse)
async def list_categories(request: Request):
    """Get list of categories for the current tenant."""
    tenant_id = get_tenant_id(request)
    conn = None

    try:
        conn = await get_db_connection()

        query = """
            SELECT name
            FROM item_categories
            WHERE tenant_id = $1
            ORDER BY name ASC
        """
        rows = await conn.fetch(query, tenant_id)
        categories = [row["name"] for row in rows]

        return CategoryListResponse(success=True, categories=categories)

    except Exception as e:
        logger.error(f"Error listing categories: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if conn:
            await conn.close()


@router.post("/items/categories", response_model=CreateCategoryResponse)
async def create_category(request: Request, body: CreateCategoryRequest):
    """
    Register a new category. Categories are stored as strings on products.
    This endpoint validates uniqueness and returns the category name. X-Idempotency-Key opsional.
    """
    ctx = get_user_context(request)
    tenant_id = ctx["tenant_id"]
    conn = None

    try:
        conn = await get_db_connection()
        async with conn.transaction():
            async def kerja():
                # Simpan ke registry item_categories. Unik case-insensitive; casing input pertama menang.
                inserted = await conn.fetchval(
                    """INSERT INTO item_categories (tenant_id, name)
                       VALUES ($1, $2)
                       ON CONFLICT (tenant_id, lower(name)) DO NOTHING
                       RETURNING name""",
                    tenant_id,
                    body.name.strip(),
                )
                if inserted is None:
                    raise HTTPException(status_code=409, detail="Kategori sudah ada")
                return {"success": True, "message": f"Kategori '{inserted}' dibuat", "category": inserted}
            r = await BA.idem_aksi(conn, ctx, request, "ITEM_KATEGORI_BUAT", body.name.strip().lower(), {"name": body.name.strip()}, kerja)
        return CreateCategoryResponse(**r)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating category: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if conn:
            await conn.close()


@router.get("/items/autocomplete")
async def autocomplete_items(
    request: Request,
    q: str = Query(default="", description="Search query"),
    limit: int = Query(20, ge=1, le=50),
    item_type: Optional[str] = Query(None, description="Filter by item type"),
):
    """Quick item search for autocomplete in forms."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()
        async with pool.acquire() as conn:
            # BUG-181: per kata (semua kata ada, urutan bebas, spasi ganda dirapikan), % _ di-escape; SATU fungsi dgn daftar barang
            cari_sql, cari_params = syarat_kata(q, ("p.nama_produk", "p.item_code", "p.sku"), 3)
            type_filter = ""
            params = [ctx["tenant_id"], limit, *cari_params]
            if item_type:
                type_filter = f" AND item_type = ${len(params) + 1}"
                params.append(item_type)

            rows = await conn.fetch(
                f"""
                SELECT p.id, p.item_code as code, p.nama_produk as name, p.item_type, p.base_unit as unit, p.sales_price as selling_price, p.purchase_price, p.track_batches, p.track_expiry,
                       p.sales_tax, p.sales_tax_id, st.name AS sales_tax_name, st.rate AS sales_tax_rate,
                       p.purchase_tax, p.purchase_tax_id, pt.name AS purchase_tax_name, pt.rate AS purchase_tax_rate
                FROM products p
                LEFT JOIN tax_codes st ON st.id = p.sales_tax_id
                LEFT JOIN tax_codes pt ON pt.id = p.purchase_tax_id
                WHERE p.tenant_id = $1
                  {cari_sql}
                  AND p.status = 'active'
                  AND p.deleted_at IS NULL
                  {type_filter}
                ORDER BY name ASC
                LIMIT $2
            """,
                *params,
            )

            # Pre-fetch tax_codes for legacy string resolution
            all_tax_codes = await conn.fetch(
                "SELECT id, code, name, rate FROM tax_codes WHERE tenant_id = $1 AND is_active = true",
                ctx["tenant_id"],
            )
            tax_code_map = {}
            for tc in all_tax_codes:
                # Map by code (e.g. "PPN-12-OUT"), and legacy patterns (e.g. "PPN_12", "PPN 12%")
                tax_code_map[tc["code"].lower()] = tc
                tax_code_map[tc["name"].lower()] = tc

            def resolve_legacy_tax(legacy_str):
                """Resolve legacy string like 'PPN_12' or 'PPN 12%' to tax_code."""
                if not legacy_str:
                    return None
                key = legacy_str.lower().strip()
                if key in tax_code_map:
                    return tax_code_map[key]
                # Try common patterns: PPN_12 → ppn 12%, PPN_11 → ppn 11%
                normalized = (
                    key.replace("_", " ").replace("ppn ", "ppn ").replace("%", "")
                )
                for tc_key, tc in tax_code_map.items():
                    if normalized in tc_key or tc_key in normalized:
                        return tc
                return None

            items = []
            for row in rows:
                item = {
                    "id": str(row["id"]),
                    "code": row["code"],
                    "name": row["name"],
                    "type": row["item_type"],
                    "unit": row["unit"],
                    "sales_price": row["selling_price"],
                    "selling_price": row["selling_price"],
                    "purchase_price": row["purchase_price"],
                    "track_batches": row.get("track_batches", False),
                    "track_expiry": row.get("track_expiry", False),
                    "sales_tax": row.get("sales_tax"),
                    "sales_tax_id": str(row["sales_tax_id"])
                    if row.get("sales_tax_id")
                    else None,
                    "sales_tax_name": row.get("sales_tax_name"),
                    "sales_tax_rate": float(row["sales_tax_rate"])
                    if row.get("sales_tax_rate")
                    else None,
                    "purchase_tax": row.get("purchase_tax"),
                    "purchase_tax_id": str(row["purchase_tax_id"])
                    if row.get("purchase_tax_id")
                    else None,
                    "purchase_tax_name": row.get("purchase_tax_name"),
                    "purchase_tax_rate": float(row["purchase_tax_rate"])
                    if row.get("purchase_tax_rate")
                    else None,
                }
                # Resolve legacy strings when UUID is missing
                if not item["sales_tax_id"] and item["sales_tax"]:
                    tc = resolve_legacy_tax(item["sales_tax"])
                    if tc:
                        item["sales_tax_id"] = str(tc["id"])
                        item["sales_tax_name"] = tc["name"]
                        item["sales_tax_rate"] = float(tc["rate"])
                if not item["purchase_tax_id"] and item["purchase_tax"]:
                    tc = resolve_legacy_tax(item["purchase_tax"])
                    if tc:
                        item["purchase_tax_id"] = str(tc["id"])
                        item["purchase_tax_name"] = tc["name"]
                        item["purchase_tax_rate"] = float(tc["rate"])
                items.append(item)
            return {"success": True, "items": items}
    except Exception as e:
        logger.error(f"Error in autocomplete: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Autocomplete failed")


# =============================================================================
# ITEMS NEXT CODE (for form auto-generation)
# =============================================================================


@router.get("/items/next-code")
async def get_next_item_code(
    request: Request,
    item_type: str = Query(
        "goods", description="Item type: goods, service, non_inventory"
    ),
):
    """Get the next available item code for auto-generation."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()
        async with pool.acquire() as conn:
            # Prefix based on item type (Indonesian)
            prefix_map = {
                "goods": "BRG",  # Barang
                "service": "JSA",  # Jasa
                "non_inventory": "NIN",  # Non-Inventory
            }
            prefix = prefix_map.get(item_type, "BRG")

            row = await conn.fetchrow(
                """
                SELECT item_code as code FROM products
                WHERE tenant_id = $1 AND item_code LIKE $2
                ORDER BY item_code DESC LIMIT 1
            """,
                ctx["tenant_id"],
                f"{prefix}-%",
            )

            import re

            if row and row["code"]:
                match = re.search(r"([0-9]+)$", row["code"])
                if match:
                    num = int(match.group(1)) + 1
                    next_code = f"{prefix}-{num:04d}"
                else:
                    next_code = f"{prefix}-0001"
            else:
                next_code = f"{prefix}-0001"

            return {"success": True, "next_code": next_code}
    except Exception as e:
        logger.error(f"Error getting next code: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get next code")


# =============================================================================
# ITEMS EXPORT
# =============================================================================


@router.get("/items/export")
async def export_items(
    request: Request,
    format: str = Query("csv", description="Export format: csv, xlsx"),
):
    """Export items to CSV or Excel format."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT
                    item_code as code, nama_produk as name, item_type, base_unit as unit, sku,
                    sales_price as selling_price, purchase_price,
                    COALESCE(
                        (SELECT SUM(il.quantity_in) - SUM(il.quantity_out) FROM inventory_ledger il
                         WHERE il.product_id = products.id AND il.tenant_id = CAST(products.tenant_id AS TEXT)),
                        0
                    ) as current_stock,
                    kategori as category, deskripsi as description,
                    CASE WHEN status = 'active' THEN true ELSE false END as is_active
                FROM products
                WHERE tenant_id = $1 AND deleted_at IS NULL
                ORDER BY item_code ASC
            """,
                ctx["tenant_id"],
            )

            if format == "csv":
                import io
                import csv

                output = io.StringIO()
                writer = csv.writer(output)
                writer.writerow(
                    [
                        "Code",
                        "Name",
                        "Type",
                        "Unit",
                        "SKU",
                        "Selling Price",
                        "Purchase Price",
                        "Stock",
                        "Category",
                        "Description",
                        "Active",
                    ]
                )
                for row in rows:
                    writer.writerow(
                        [
                            row["code"],
                            row["name"],
                            row["item_type"],
                            row["unit"],
                            row["sku"],
                            row["selling_price"],
                            row["purchase_price"],
                            row["current_stock"],
                            row["category"],
                            row["description"],
                            "Yes" if row["is_active"] else "No",
                        ]
                    )
                content = output.getvalue()
                from fastapi.responses import Response

                return Response(
                    content=content,
                    media_type="text/csv",
                    headers={
                        "Content-Disposition": "attachment; filename=items_export.csv"
                    },
                )
            else:
                return {
                    "success": True,
                    "message": "Export format not supported yet",
                    "count": len(rows),
                }
    except Exception as e:
        logger.error(f"Error exporting items: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Export failed")


# =============================================================================
# ITEMS BULK IMPORT
# =============================================================================


@router.post("/items/bulk-import")
async def bulk_import_items(request: Request):
    """
    Bulk import items from parsed CSV data.
    Expects JSON body with array of item data.
    """
    try:
        ctx = get_user_context(request)
        pool = await get_pool()
        body = await request.json()
        items_data = body.get("items", [])

        if not items_data:
            raise HTTPException(status_code=400, detail="No items provided")

        created = 0
        updated = 0
        errors = []

        async with pool.acquire() as conn:
            async with conn.transaction():
                for idx, item in enumerate(items_data):
                    try:
                        code = item.get("code")
                        name = item.get("name")

                        if not name:
                            errors.append({"row": idx + 1, "error": "Name is required"})
                            continue

                        # Check if item exists by code
                        existing = None
                        if code:
                            existing = await conn.fetchrow(
                                "SELECT id FROM products WHERE tenant_id = $1 AND item_code = $2",
                                ctx["tenant_id"],
                                code,
                            )

                        if existing:
                            # Q-018 (26 Sep 2026): impor massal tak boleh diam-diam mengubah tipe barang yang punya
                            # JEBAKAN nyata (pendapatan tertunda / riwayat ledger). Ubah tipe lain (niat sah pemilik:
                            # semua barang non_inventory) tetap boleh. Field lain tetap diperbarui; barisnya dilaporkan.
                            # 27 Sep: aturan per ARAH yang SAMA dengan form PUT (_alasan_tolak_ubah_tipe); pelacakan
                            # ikut tipe (dulu impor hanya mengganti item_type -> non_inventory tetap dilacak).
                            tipe_baru = item.get("type")
                            lacak_baru = None
                            if tipe_baru is not None:
                                lama = await conn.fetchrow(
                                    "SELECT item_type, track_inventory FROM products WHERE id = $1 AND tenant_id = $2",
                                    existing["id"], ctx["tenant_id"],
                                )
                                tipe_lama, lacak_lama = lama["item_type"], bool(lama["track_inventory"])
                                lacak_baru = _lacak_efektif(tipe_baru, None, tipe_lama, lacak_lama)
                                alasan = await _alasan_tolak_ubah_tipe(
                                    conn, ctx["tenant_id"], existing["id"], tipe_lama, lacak_lama, tipe_baru, lacak_baru
                                )
                                if alasan:
                                    errors.append({"row": idx + 1, "error": f"Tipe {tipe_lama} tidak diubah ke {tipe_baru}: {alasan} (field lain diperbarui)"})
                                    tipe_baru, lacak_baru = None, None
                            # Update existing
                            await conn.execute(
                                """
                            UPDATE products SET
                                nama_produk = $1,
                                item_type = COALESCE($2, item_type),
                                base_unit = COALESCE($3, base_unit),
                                sales_price = COALESCE($4, sales_price),
                                purchase_price = COALESCE($5, purchase_price),
                                track_inventory = COALESCE($8, track_inventory),
                                bisa_dikirim = CASE WHEN COALESCE($2, item_type) = 'service' THEN false ELSE bisa_dikirim END,
                                updated_at = NOW()
                            WHERE id = $6 AND tenant_id = $7
                        """,
                                name,
                                tipe_baru,
                                item.get("unit"),
                                item.get("selling_price"),
                                item.get("purchase_price"),
                                existing["id"],
                                ctx["tenant_id"],
                                lacak_baru,
                            )
                            updated += 1
                        else:
                            # Create new
                            import uuid as uuid_mod

                            new_id = str(uuid_mod.uuid4())
                            await conn.execute(
                                """
                            INSERT INTO products (
                                id, tenant_id, item_code, nama_produk, satuan, item_type, base_unit,
                                sales_price, purchase_price, status, created_at, updated_at
                            ) VALUES ($1, $2, $3, $4, $6, $5, $6, $7, $8, 'active', NOW(), NOW())
                        """,
                                new_id,
                                ctx["tenant_id"],
                                code,
                                name,
                                item.get("type", "product"),
                                item.get("unit", "pcs"),
                                item.get("selling_price", 0),
                                item.get("purchase_price", 0),
                            )
                            created += 1

                    except Exception as row_error:
                        errors.append({"row": idx + 1, "error": str(row_error)})

        return {
            "success": True,
            "created": created,
            "updated": updated,
            "errors": errors,
            "total_processed": len(items_data),
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in bulk import: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Bulk import failed")


# =============================================================================
# ITEMS STATUS UPDATE
# =============================================================================


@router.patch("/items/{item_id}/status")
async def update_item_status(request: Request, item_id: str):
    """Update item active/inactive status (+ baris riwayat; nonaktif ber-stok BOLEH, peringatannya di /items/{id}/status/preview).
    X-Idempotency-Key opsional."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()
        body = await request.json()
        is_active = bool(body.get("is_active", True))

        async with pool.acquire() as conn:
            async with conn.transaction():
                ada = await conn.fetchval("SELECT 1 FROM products WHERE id = $1::uuid AND tenant_id = $2 AND deleted_at IS NULL", item_id, ctx["tenant_id"])
                if not ada:
                    raise HTTPException(status_code=404, detail="Item not found")

                async def kerja():
                    await BA.terapkan_status(conn, ctx["tenant_id"], item_id, is_active, ctx.get("user_id"),
                                             getattr(request.state, "user", {}).get("username"))
                    return {"success": True, "message": "Status updated", "is_active": is_active}
                return await BA.idem_aksi(conn, ctx, request, "ITEM_STATUS", item_id, {"is_active": is_active}, kerja)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating status: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to update status")


# =============================================================================
# DEFAULT ACCOUNTS (pre-resolved for form auto-fill)
# =============================================================================


@router.get("/items/default-accounts")
async def get_default_accounts(request: Request):
    """
    Returns pre-resolved default account IDs for form auto-fill.
    One API call replaces heuristic matching in frontend useEffect.
    """
    tenant_id = get_tenant_id(request)
    conn = None
    try:
        conn = await get_db_connection()

        async def resolve(account_type, code_prefix, name_hint):
            row = await conn.fetchrow(
                """SELECT id, account_code, name FROM chart_of_accounts
                   WHERE tenant_id = $1 AND is_active = true AND NOT is_header
                     AND account_code LIKE $2
                   ORDER BY account_code ASC LIMIT 1""",
                tenant_id,
                f"{code_prefix}%",
            )
            if row:
                return {
                    "id": str(row["id"]),
                    "code": row["account_code"],
                    "name": row["name"],
                }
            row = await conn.fetchrow(
                """SELECT id, account_code, name FROM chart_of_accounts
                   WHERE tenant_id = $1 AND is_active = true AND NOT is_header
                     AND account_type = $2 AND name ILIKE $3
                   ORDER BY account_code ASC LIMIT 1""",
                tenant_id,
                account_type,
                f"%{name_hint}%",
            )
            if row:
                return {
                    "id": str(row["id"]),
                    "code": row["account_code"],
                    "name": row["name"],
                }
            return None

        sales = await resolve("REVENUE", "4-10100", "penjualan")
        service_sales = await resolve("REVENUE", "4-10150", "jasa")
        if not service_sales:
            service_sales = sales  # fallback to main sales
        purchase = await resolve("EXPENSE", "5-20900", "lain")
        if not purchase:
            purchase = await resolve("EXPENSE", "5-20", "operasional")
        inventory = await resolve("ASSET", "1-10600", "persediaan")
        cogs = await resolve("COGS", "5-10100", "hpp")
        if not cogs:
            cogs = await resolve("EXPENSE", "5-10100", "harga pokok")

        return {
            "success": True,
            "data": {
                "default_sales_account_id": sales["id"] if sales else None,
                "default_sales_account": sales,
                "default_service_sales_account_id": (service_sales or sales or {}).get(
                    "id"
                ),
                "default_service_sales_account": service_sales or sales,
                "default_purchase_account_id": purchase["id"] if purchase else None,
                "default_purchase_account": purchase,
                "default_inventory_account_id": inventory["id"] if inventory else None,
                "default_inventory_account": inventory,
                "default_cogs_account_id": cogs["id"] if cogs else None,
                "default_cogs_account": cogs,
                # 10 Okt (F3): default_item_type + default_bisa_dikirim panel Barang, diputuskan server per tenant
                **(await bawaan_panel_barang(conn, tenant_id)),
            },
        }
    except Exception as e:
        logger.error(f"Error resolving default accounts: {e}")
        raise HTTPException(
            status_code=500, detail="Failed to resolve default accounts"
        )
    finally:
        if conn:
            await conn.close()


# =============================================================================
# ITEMS STOCK ADJUSTMENT
# =============================================================================


@router.get("/items/{item_id}/activity", response_model=ItemActivityResponse)
async def get_item_activity(
    request: Request,
    item_id: UUID,
    status: Optional[str] = Query(
        None, description="Filter by status: active, inactive"
    ),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    """Get activity log / audit trail for an item."""
    tenant_id = get_tenant_id(request)
    conn = None

    try:
        conn = await get_db_connection()

        # Verify item exists
        item_exists = await conn.fetchval(
            "SELECT id FROM products WHERE id = $1 AND tenant_id = $2",
            str(item_id),
            tenant_id,
        )
        if not item_exists:
            raise HTTPException(status_code=404, detail="Item not found")

        # Get total count
        total = await conn.fetchval(
            "SELECT COUNT(*) FROM item_activities WHERE item_id = $1 AND tenant_id = $2",
            str(item_id),
            tenant_id,
        )

        # Get activities
        query = """
            SELECT ia.id, ia.type, ia.description, ia.details, ia.actor_name, ia.timestamp,
                   NULLIF(up.display_name, '') AS profil_nama, NULLIF(u.name, '') AS pengguna_nama
            FROM item_activities ia
            LEFT JOIN "User" u ON u.id::text = ia.actor_id::text
            LEFT JOIN user_profiles up ON up.user_id::text = ia.actor_id::text
            WHERE ia.item_id = $1 AND ia.tenant_id = $2
            ORDER BY ia.timestamp DESC
            LIMIT $3 OFFSET $4
        """
        rows = await conn.fetch(query, str(item_id), tenant_id, limit, offset)

        activities = [
            ItemActivity(
                id=str(row["id"]),
                type=row["type"],
                description=BT.norm_aktivitas(row["description"]),
                details=row.get("details"),
                actor_name=BT.nama_tampil(row["profil_nama"], row["pengguna_nama"], row.get("actor_name")),
                timestamp=row["timestamp"].isoformat() if row["timestamp"] else None,
            )
            for row in rows
        ]

        return ItemActivityResponse(
            success=True,
            activities=activities,
            total=total or 0,
            has_more=(offset + limit) < (total or 0),
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting item activity: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if conn:
            await conn.close()


# =============================================================================
# =============================================================================
# STOCK MOVEMENT HISTORY (Tab Riwayat Stok)
# =============================================================================


@router.get("/items/{item_id}/history")
async def get_item_history(
    request: Request,
    item_id: UUID,
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
):
    """Get stock movement history from inventory ledger for an item."""
    tenant_id = get_tenant_id(request)
    conn = None
    offset = (page - 1) * limit

    try:
        conn = await get_db_connection()

        # Verify item exists
        item = await conn.fetchrow(
            "SELECT id, item_code, nama_produk FROM products WHERE id = $1 AND tenant_id = $2",
            str(item_id),
            tenant_id,
        )
        if not item:
            raise HTTPException(status_code=404, detail="Item not found")

        # Get total count
        total = await conn.fetchval(
            "SELECT COUNT(*) FROM inventory_ledger WHERE product_id = $1 AND tenant_id = $2",
            str(item_id),
            tenant_id,
        )

        # Get stock movements from inventory_ledger with counterparty info
        # f-string: kosakata ledger disisipkan (dulu string BIASA -> "{sql_daftar(...)}" terkirim mentah -> 500)
        query = f"""
            SELECT
                il.id,
                il.movement_type,
                il.movement_date,
                il.source_type,
                il.source_id,
                il.source_number,
                il.quantity_in,
                il.quantity_out,
                -- BUG-099: saldo berjalan DIHITUNG SAAT BACA (Law 1/11), urutan tampilan; kolom quantity_balance = snapshot
                -- tulis-waktu yang basi bila ada gerak bertanggal mundur -> tidak dipakai tampilan, tidak ditulis ulang (Rule 8)
                SUM(COALESCE(il.quantity_in, 0) - COALESCE(il.quantity_out, 0))
                    OVER (ORDER BY il.movement_date, il.created_at, il.id) AS saldo_berjalan,
                il.unit_cost,
                il.total_cost,
                il.average_cost,
                il.warehouse_id,
                il.batch_id,
                il.notes,
                il.created_at,
                il.created_by,
                CASE
                    WHEN il.source_type IN {sql_daftar(KELUAR_JUAL_FAKTUR)} THEN si.customer_name
                    WHEN il.source_type IN ('BILL', 'PURCHASE_INVOICE') THEN b.vendor_name
                    ELSE NULL
                END AS counterparty
            FROM inventory_ledger il
            LEFT JOIN sales_invoices si
                ON il.source_type IN {sql_daftar(KELUAR_JUAL_FAKTUR)}
                AND il.source_id::text = si.id::text
                AND si.tenant_id = $2
            LEFT JOIN bills b
                ON il.source_type IN ('BILL', 'PURCHASE_INVOICE')
                AND il.source_id::text = b.id::text
                AND b.tenant_id = $2
            WHERE il.product_id = $1 AND il.tenant_id = $2
            ORDER BY il.movement_date DESC, il.created_at DESC, il.id DESC
            LIMIT $3 OFFSET $4
        """
        rows = await conn.fetch(query, str(item_id), tenant_id, limit, offset)

        movements = []
        for row in rows:
            movements.append(
                {
                    "id": str(row["id"]),
                    "movement_type": row["movement_type"],
                    "jenis_text": BT.jenis_text(row["movement_type"], row["source_type"]),
                    "movement_date": row["movement_date"].isoformat()
                    if row["movement_date"]
                    else None,
                    "source_type": row["source_type"],
                    "source_id": str(row["source_id"]) if row["source_id"] else None,
                    "source_number": row["source_number"],
                    "quantity_in": row["quantity_in"] or 0,
                    "quantity_out": row["quantity_out"] or 0,
                    "quantity_balance": row["saldo_berjalan"] or 0,  # BUG-099: saldo berjalan saat baca (bukan snapshot)
                    "unit_cost": float(_rp(row["unit_cost"])),  # BUG-038: dulu int() membuang sen
                    "total_cost": float(_rp(row["total_cost"])),
                    "average_cost": float(_rp(row["average_cost"])) if row["average_cost"] else None,
                    "warehouse_id": str(row["warehouse_id"])
                    if row["warehouse_id"]
                    else None,
                    "batch_id": str(row["batch_id"]) if row["batch_id"] else None,
                    "notes": row["notes"],
                    "created_at": row["created_at"].isoformat()
                    if row["created_at"]
                    else None,
                    "counterparty": row["counterparty"],
                }
            )

        return {
            "success": True,
            "data": movements,
            "total": total or 0,
            "page": page,
            "limit": limit,
            "has_more": (offset + limit) < (total or 0),
            "item": {
                "id": str(item["id"]),
                "code": item["item_code"],
                "name": item["nama_produk"],
            },
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting item history: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get item history")
    finally:
        if conn:
            await conn.close()


# GET ITEM DETAIL
# =============================================================================


@router.get("/items/{item_id}", response_model=ItemDetailResponse)
async def get_item(request: Request, item_id: UUID):
    """Get detailed information about a specific item including unit conversions."""
    tenant_id = get_tenant_id(request)
    conn = None

    try:
        conn = await get_db_connection()

        # Get item - stock data derived from inventory_ledger (Law 16 compliance)
        item_query = """
            SELECT p.*, COALESCE(per.jumlah, 0) as current_stock, per.total_nilai as stock_value, per.transaction_count,
                   sa.account_code AS sales_acct_code, sa.name AS sales_acct_name,
                   pa.account_code AS purchase_acct_code, pa.name AS purchase_acct_name,
                   ia.account_code AS inventory_acct_code, ia.name AS inventory_acct_name,
                   ca.account_code AS cogs_acct_code, ca.name AS cogs_acct_name
            FROM products p
            LEFT JOIN chart_of_accounts sa ON sa.id = p.sales_account_id
            LEFT JOIN chart_of_accounts pa ON pa.id = p.purchase_account_id
            LEFT JOIN chart_of_accounts ia ON ia.id = p.inventory_account_id
            LEFT JOIN chart_of_accounts ca ON ca.id = p.cogs_account_id
            LEFT JOIN LATERAL (
                            SELECT COALESCE(SUM(il.quantity_in) - SUM(il.quantity_out), 0) as jumlah,
                            COALESCE(SUM(il.total_cost * SIGN(il.quantity_in - il.quantity_out)), 0) as total_nilai,
                            (
                                SELECT COUNT(*) FROM (
                                    SELECT 1 FROM inventory_ledger WHERE product_id = p.id AND tenant_id = p.tenant_id
                                    UNION ALL
                                    SELECT 1 FROM sales_invoice_items WHERE item_id = p.id
                                    UNION ALL
                                    SELECT 1 FROM bill_items WHERE product_id = p.id
                                    UNION ALL
                                    SELECT 1 FROM stock_adjustment_items WHERE product_id = p.id
                                    UNION ALL
                                    SELECT 1 FROM stock_transfer_items WHERE item_id = p.id
                                    UNION ALL
                                    SELECT 1 FROM production_orders WHERE product_id = p.id AND tenant_id = p.tenant_id
                                    UNION ALL
                                    SELECT 1 FROM recipes WHERE product_id = p.id AND tenant_id = p.tenant_id
                                ) txns
                            ) as transaction_count
                            FROM inventory_ledger il
                            WHERE il.product_id = p.id AND il.tenant_id = CAST(p.tenant_id AS TEXT)
                        ) per ON true
            WHERE p.id = $1 AND p.tenant_id = $2 AND p.deleted_at IS NULL
        """
        row = await conn.fetchrow(item_query, str(item_id), tenant_id)

        if not row:
            raise HTTPException(status_code=404, detail="Item not found")

        # Get unit conversions
        conversions_query = """
            SELECT * FROM unit_conversions
            WHERE product_id = $1 AND tenant_id = $2 AND is_active = true
            ORDER BY conversion_factor ASC
        """
        conversion_rows = await conn.fetch(conversions_query, str(item_id), tenant_id)
        # BUG-086: tanda TERKUNCI dari penentu YANG SAMA dengan penolak PUT (satuan_dasar.konversi_terpakai / dasar_terpakai)
        from ..services import satuan_dasar as _SDK
        terpakai = {str(c["id"]): await _SDK.konversi_terpakai(conn, tenant_id, str(item_id), c["conversion_unit"]) for c in conversion_rows}
        base_unit_terpakai = await _SDK.dasar_terpakai(conn, tenant_id, str(item_id))
        sb = (await SB.hitung(conn, tenant_id, [item_id], await tanggal_dokumen(conn, tenant_id)))["barang"].get(str(item_id), {}) \
            if BD.dilacak(row) else {}

        conversions = [
            {
                "id": str(c["id"]),
                "base_unit": c["base_unit"],
                "conversion_unit": c["conversion_unit"],
                "conversion_factor": SD_faktor_json(c["conversion_factor"]),
                "purchase_price": c.get("purchase_price"),
                "sales_price": c.get("sales_price"),
                "is_active": c.get("is_active", True),
                "terpakai": terpakai[str(c["id"])],
            }
            for c in conversion_rows
        ]

        return ItemDetailResponse(
            success=True,
            data={
                "id": str(row["id"]),
                "name": row["nama_produk"],
                "item_type": row.get("item_type", "goods"),
                "track_inventory": row.get("track_inventory", True),
                "bisa_dikirim": bool(row.get("bisa_dikirim") or False),
                "base_unit": row.get("base_unit") or row["satuan"],
                "base_unit_terpakai": base_unit_terpakai,  # BUG-086: True -> satuan dasar tak bisa diganti (409 SATUAN_DASAR_TERPAKAI)
                "barcode": row.get("barcode"),
                "kategori": row.get("kategori"),
                "deskripsi": row.get("deskripsi"),
                "is_returnable": row.get("is_returnable", True),
                "track_batches": row.get("track_batches", False),
                "track_expiry": row.get("track_expiry", False),
                "default_expiry_days": row.get("default_expiry_days"),
                "image_url": row.get("image_url"),
                "reorder_level": row["reorder_level"]
                if row.get("reorder_level")
                else None,
                "reorder_point": int(row["reorder_level"])
                if row.get("reorder_level")
                else 0,
                "preferred_vendor_id": str(row["preferred_vendor_id"])
                if row.get("preferred_vendor_id")
                else None,
                "sales_account": f"{row['sales_acct_code']} - {row['sales_acct_name']}"
                if row.get("sales_acct_code")
                else None,
                "sales_account_name": f"{row['sales_acct_code']} - {row['sales_acct_name']}"
                if row.get("sales_acct_code")
                else None,
                "purchase_account": f"{row['purchase_acct_code']} - {row['purchase_acct_name']}"
                if row.get("purchase_acct_code")
                else None,
                "purchase_account_name": f"{row['purchase_acct_code']} - {row['purchase_acct_name']}"
                if row.get("purchase_acct_code")
                else None,
                "inventory_account_name": f"{row['inventory_acct_code']} - {row['inventory_acct_name']}"
                if row.get("inventory_acct_code")
                else None,
                "cogs_account_name": f"{row['cogs_acct_code']} - {row['cogs_acct_name']}"
                if row.get("cogs_acct_code")
                else None,
                "sales_account_id": str(row["sales_account_id"])
                if row.get("sales_account_id")
                else None,
                "purchase_account_id": str(row["purchase_account_id"])
                if row.get("purchase_account_id")
                else None,
                "inventory_account_id": str(row["inventory_account_id"])
                if row.get("inventory_account_id")
                else None,
                "cogs_account_id": str(row["cogs_account_id"])
                if row.get("cogs_account_id")
                else None,
                "item_code": row.get("item_code"),
                "sku": row.get("sku"),
                "costing_method": row.get("costing_method", "weighted_average"),
                "opening_stock": row["opening_stock"]
                if row.get("opening_stock")
                else None,
                "opening_stock_rate": int(row["opening_stock_rate"])
                if row.get("opening_stock_rate")
                else None,
                "opening_stock_date": str(row["opening_stock_date"])
                if row.get("opening_stock_date")
                else None,
                "for_sales": row.get("for_sales", True),
                "for_purchases": row.get("for_purchases", True),
                "warehouse_id": str(row["warehouse_id"])
                if row.get("warehouse_id")
                else None,
                "sales_tax": row.get("sales_tax"),
                "purchase_tax": row.get("purchase_tax"),
                "sales_price": row.get("sales_price") or row.get("harga_jual"),
                "purchase_price": row.get("purchase_price"),
                "current_stock": row.get("current_stock"),
                "stock_value": row.get("stock_value"),
                **hpp_dan_margin(row.get("stock_value"), row.get("current_stock"), row.get("sales_price") or row.get("harga_jual")),
                "transaction_count": int(row.get("transaction_count", 0)),
                "aksi_semua": BD.aksi_semua(row, row.get("current_stock")),
                "position_text": BD.position_text(row, row.get("current_stock"), sb.get("bisa_dijual"),
                                                  sb.get("satuan_dasar") or row.get("base_unit") or row.get("satuan")),
                "position_text_ket": BD.position_text_ket(row, row.get("current_stock"), sb.get("bisa_dijual"),
                                                          sb.get("satuan_dasar") or row.get("base_unit") or row.get("satuan")),
                "bisa_dijual": sb.get("bisa_dijual"),
                "keadaan": BA.keadaan(row), "keadaan_text": BA.keadaan_text(row),
                "aksi_tersedia": BA.aksi_tersedia_detail(row),
                "conversions": conversions,
                "created_at": row["created_at"].isoformat()
                if row["created_at"]
                else None,
                "updated_at": row["updated_at"].isoformat()
                if row["updated_at"]
                else None,
            },
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting item: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if conn:
            await conn.close()


# =============================================================================
# TRANSACTIONS (Tab Riwayat)
# =============================================================================


@router.get("/items/{item_id}/transactions", response_model=ItemTransactionsResponse)
async def get_item_transactions(
    request: Request,
    item_id: UUID,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    """Get transaction history for an item (purchases, sales, adjustments)."""
    tenant_id = get_tenant_id(request)
    conn = None

    try:
        conn = await get_db_connection()

        # Verify item exists
        item_exists = await conn.fetchval(
            "SELECT id FROM products WHERE id = $1 AND tenant_id = $2",
            str(item_id),
            tenant_id,
        )
        if not item_exists:
            raise HTTPException(status_code=404, detail="Item not found")

        # Query transaction history from inventory_ledger (Pure Ledger)
        tx_query = f"""
            SELECT
                il.id::text,
                to_char(il.movement_date, 'YYYY-MM-DD') as date,
                CASE
                    WHEN il.source_type = 'BILL' THEN 'pembelian'
                    WHEN il.source_type IN {sql_daftar(KELUAR_JUAL)} THEN 'penjualan'
                    WHEN il.source_type = 'OPENING_BALANCE' THEN 'saldo_awal'
                    WHEN il.source_type IN {sql_daftar(BATAL_JUAL)} THEN 'void_penjualan'
                    WHEN il.source_type = 'STOCK_ADJUSTMENT' THEN 'penyesuaian'
                    ELSE LOWER(il.source_type)
                END as transaction_type,
                COALESCE(il.source_number, il.source_id::text) as document_number,
                CASE
                    WHEN il.quantity_in > 0 THEN il.quantity_in
                    ELSE -il.quantity_out
                END as qty_change,
                il.unit_cost as unit_price,
                il.total_cost as total,
                il.notes
            FROM inventory_ledger il
            WHERE il.product_id = $1::uuid AND il.tenant_id = $2
            ORDER BY il.movement_date DESC, il.created_at DESC, il.id DESC
            LIMIT $3 OFFSET $4
        """
        rows = await conn.fetch(tx_query, str(item_id), tenant_id, limit, offset)

        # Get total count
        count = await conn.fetchval(
            """SELECT COUNT(*) FROM inventory_ledger il
               WHERE il.product_id = $1::uuid AND il.tenant_id = $2""",
            str(item_id),
            tenant_id,
        )

        transactions = [
            ItemTransaction(
                id=row.get("id"),
                date=row["date"] or "",
                transaction_type=row.get("transaction_type", "unknown"),
                document_number=row.get("document_number"),
                qty_change=row.get("qty_change") or 0,
                unit_price=int(row["unit_price"]) if row.get("unit_price") else None,
                total=int(row["total"]) if row.get("total") else None,
                notes=row.get("notes"),
            )
            for row in rows
        ]

        return ItemTransactionsResponse(
            success=True,
            transactions=transactions,
            total=count or 0,
            has_more=(offset + limit) < (count or 0),
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting item transactions: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if conn:
            await conn.close()


# =============================================================================
# RELATED DOCUMENTS (Tab Terkait)
# =============================================================================


@router.get("/items/{item_id}/related", response_model=ItemRelatedResponse)
async def get_item_related(request: Request, item_id: UUID):
    """Get related documents (invoices, bills) for an item."""
    tenant_id = get_tenant_id(request)
    conn = None

    try:
        conn = await get_db_connection()

        # Verify item exists
        item_exists = await conn.fetchval(
            "SELECT id FROM products WHERE id = $1 AND tenant_id = $2",
            str(item_id),
            tenant_id,
        )
        if not item_exists:
            raise HTTPException(status_code=404, detail="Item not found")

        # Related invoices (sales)
        invoices = []
        try:
            inv_query = """
                SELECT
                    si.id::text,
                    si.invoice_number as document_number,
                    si.invoice_date::text as date,
                    si.customer_name as counterparty,
                    sii.quantity as qty,
                    sii.unit_price,
                    sii.total as total,
                    si.status
                FROM sales_invoice_items sii
                JOIN sales_invoices si ON si.id = sii.invoice_id
                WHERE sii.item_id = $1 AND si.tenant_id = $2
                ORDER BY si.invoice_date DESC
                LIMIT 20
            """
            inv_rows = await conn.fetch(inv_query, str(item_id), tenant_id)
            invoices = [
                RelatedDocument(
                    id=row["id"],
                    document_type="invoice",
                    document_number=row.get("document_number"),
                    date=row.get("date"),
                    counterparty=row.get("counterparty"),
                    qty=row["qty"] if row.get("qty") else None,
                    unit_price=int(row["unit_price"])
                    if row.get("unit_price")
                    else None,
                    total=int(row["total"]) if row.get("total") else None,
                    status=row.get("status"),
                )
                for row in inv_rows
            ]
        except Exception as e:
            logger.warning(f"Could not fetch related invoices: {e}")

        # Related bills (purchases)
        bills = []
        try:
            bill_query = """
                SELECT
                    b.id::text,
                    b.invoice_number as document_number,
                    b.issue_date::text as date,
                    b.vendor_name as counterparty,
                    bi.quantity as qty,
                    bi.unit_price,
                    bi.total as total,
                    b.status
                FROM bill_items bi
                JOIN bills b ON b.id = bi.bill_id
                WHERE bi.product_id = $1 AND b.tenant_id = $2
                ORDER BY b.issue_date DESC
                LIMIT 20
            """
            bill_rows = await conn.fetch(bill_query, str(item_id), tenant_id)
            bills = [
                RelatedDocument(
                    id=row["id"],
                    document_type="bill",
                    document_number=row.get("document_number"),
                    date=row.get("date"),
                    counterparty=row.get("counterparty"),
                    qty=row["qty"] if row.get("qty") else None,
                    unit_price=int(row["unit_price"])
                    if row.get("unit_price")
                    else None,
                    total=int(row["total"]) if row.get("total") else None,
                    status=row.get("status"),
                )
                for row in bill_rows
            ]
        except Exception as e:
            logger.warning(f"Could not fetch related bills: {e}")

        return ItemRelatedResponse(
            success=True,
            invoices=invoices,
            bills=bills,
            purchase_orders=[],  # PO table may not exist yet
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting related documents: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if conn:
            await conn.close()


# =============================================================================
# DUPLICATE ITEM
# =============================================================================


async def _nama_salinan(conn, tenant_id: str, original_name: str) -> str:
    """Nama salinan "X (Copy)" / "X (Copy N)" yang belum dipakai (SATU penentu: penulis duplikat + pratinjau)."""
    copy_name, suffix = f"{original_name} (Copy)", 1
    while await conn.fetchval("SELECT id FROM products WHERE tenant_id = $1 AND nama_produk = $2 AND deleted_at IS NULL", tenant_id, copy_name):
        suffix += 1
        copy_name = f"{original_name} (Copy {suffix})"
    return copy_name


@router.post("/items/{item_id}/duplicate", response_model=CreateItemResponse)
async def duplicate_item(request: Request, item_id: UUID, response: Response = None):
    """Duplicate an existing item with '(Copy)' suffix. Does not copy stock or images."""
    ctx = get_user_context(request)
    tenant_id = ctx["tenant_id"]
    conn = None

    try:
        conn = await get_db_connection()

        # Fetch original item
        item_row = await conn.fetchrow(
            "SELECT * FROM products WHERE id = $1 AND tenant_id = $2",
            str(item_id),
            tenant_id,
        )
        if not item_row:
            raise HTTPException(status_code=404, detail="Item not found")

        copy_name = await _nama_salinan(conn, tenant_id, item_row["nama_produk"])

        async with conn.transaction():
            _kp, _sd, _lama = await idem_buat.mulai_aksi(conn, ctx, idem_buat.kunci_dari(request), "ITEM_DUPLICATE", str(item_id), {"duplikat": True}, response)
            if _lama is not None:
                return CreateItemResponse(**_lama)
            # Kode/SKU baru wajib unik (putusan 13 Nov): kunci per tenant + nomor berikutnya, sama dgn POST /items
            await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", f"ITEM_KODE:{tenant_id}")
            kode_baru = await _kode_otomatis(conn, tenant_id, item_row.get("item_type", "goods"))
            # Insert duplicate (without stock, image, barcode)
            new_id = await conn.fetchval(
                """
                INSERT INTO products (
                    tenant_id, nama_produk, satuan, base_unit, kategori, deskripsi,
                    item_type, track_inventory, is_returnable,
                    sales_account, purchase_account, sales_tax, purchase_tax,
                    sales_price, purchase_price, harga_jual,
                    reorder_level, preferred_vendor_id,
                    sales_account_id, purchase_account_id,
                    item_code, sku,
                    created_at, updated_at
                ) VALUES (
                    $1, $2, $3, $4, $5, $6,
                    $7, $8, $9,
                    $10, $11, $12, $13,
                    $14, $15, $16,
                    $17, $18,
                    $19, $20,
                    $21, $21,
                    NOW(), NOW()
                )
                RETURNING id
                """,
                ctx["tenant_id"],
                copy_name,
                item_row.get("satuan"),
                item_row.get("base_unit") or item_row.get("satuan"),
                item_row.get("kategori"),
                item_row.get("deskripsi"),
                item_row.get("item_type", "goods"),
                item_row.get("track_inventory", True),
                item_row.get("is_returnable", True),
                item_row.get("sales_account", "Sales"),
                item_row.get("purchase_account", "Cost of Goods Sold"),
                item_row.get("sales_tax"),
                item_row.get("purchase_tax"),
                item_row.get("sales_price"),
                item_row.get("purchase_price"),
                item_row.get("sales_price"),  # harga_jual
                item_row["reorder_level"] if item_row.get("reorder_level") else None,
                str(item_row["preferred_vendor_id"])
                if item_row.get("preferred_vendor_id")
                else None,
                str(item_row["sales_account_id"])
                if item_row.get("sales_account_id")
                else None,
                str(item_row["purchase_account_id"])
                if item_row.get("purchase_account_id")
                else None,
                kode_baru,
            )

            # Duplicate unit conversions
            conversions = await conn.fetch(
                "SELECT * FROM unit_conversions WHERE product_id = $1 AND tenant_id = $2 AND is_active = true",
                str(item_id),
                ctx["tenant_id"],
            )
            for conv in conversions:
                await conn.execute(
                    """
                    INSERT INTO unit_conversions (
                        tenant_id, product_id, base_unit, conversion_unit, conversion_factor,
                        purchase_price, sales_price, is_active, created_at, updated_at
                    ) VALUES ($1, $2, $3, $4, $5, $6, $7, true, NOW(), NOW())
                    """,
                    tenant_id,
                    new_id,
                    conv["base_unit"],
                    conv["conversion_unit"],
                    conv["conversion_factor"],
                    conv.get("purchase_price"),
                    conv.get("sales_price"),
                )

            # Harga bertingkat yang SUNGGUH dipakai harga = baris price_list_items (semua daftar, termasuk bawaan; satuan, min_quantity,
            # masa berlaku sama) -> ikut disalin (putusan MASTER 13 Nov). item_pricing (warisan yatim) TIDAK disalin. Stok/saldo awal/batch/riwayat TIDAK.
            await conn.execute(
                """INSERT INTO price_list_items (price_list_id, item_id, item_code, unit, price, min_quantity, discount_percent,
                                                 markup_percent, start_date, end_date, is_active, is_default_unit)
                   SELECT pli.price_list_id, $2::uuid, $4, pli.unit, pli.price, pli.min_quantity, pli.discount_percent,
                          pli.markup_percent, pli.start_date, pli.end_date, pli.is_active, pli.is_default_unit
                   FROM price_list_items pli JOIN price_lists pl ON pl.id = pli.price_list_id
                   WHERE pli.item_id = $1::uuid AND pl.tenant_id = $3""",
                str(item_id), str(new_id), tenant_id, kode_baru)

            # Note: No initial stock entry needed for duplicate.
            # Stock is derived from inventory_ledger movements (Law 16).
            # Duplicate starts with 0 stock until actual transactions occur.
            await BA.catat_duplikat(conn, tenant_id, str(new_id), str(item_id), item_row["nama_produk"], ctx.get("user_id"),
                                    getattr(request.state, "user", {}).get("username"))
            _resp = {"success": True, "message": f"Item '{copy_name}' berhasil diduplikasi",
                     "data": {"id": str(new_id), "name": copy_name, "item_type": item_row.get("item_type", "goods"), "source_id": str(item_id)}}
            await idem_buat.simpan(conn, ctx, _kp, _sd, "ITEM_DUPLICATE", _resp, str(item_id))

        logger.info(f"Duplicated item {item_id} -> {new_id} for tenant {tenant_id}")

        return CreateItemResponse(**_resp)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error duplicating item: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if conn:
            await conn.close()


# Alias for inventory_asset (frontend compatibility)
@router.get("/items/accounts/inventory_asset", response_model=CoaAccountsResponse)
async def list_inventory_asset_accounts(request: Request):
    """Alias for /items/accounts/inventory - frontend compatibility."""
    return await list_inventory_accounts(request)


@router.get("/items/accounts/cogs", response_model=CoaAccountsResponse)
async def list_cogs_accounts(request: Request):
    """
    Get list of COGS (Cost of Goods Sold) accounts from Chart of Accounts.

    Filters for expense accounts related to cost of goods sold.
    """
    tenant_id = get_tenant_id(request)
    conn = None

    try:
        conn = await get_db_connection()

        query = """
            SELECT id, account_code, name, account_type
            FROM chart_of_accounts
            WHERE tenant_id = $1
              AND account_type IN ('EXPENSE', 'COGS')
              AND is_active = true
              AND (
                  name ILIKE '%%hpp%%'
                  OR name ILIKE '%%harga pokok%%'
                  OR name ILIKE '%%cost of goods%%'
                  OR name ILIKE '%%cogs%%'
                  OR account_code LIKE '5-1%%'  -- Law 27: read filter
              )
            ORDER BY account_code ASC
        """
        rows = await conn.fetch(query, tenant_id)

        accounts = [
            CoaAccountOption(
                id=str(row["id"]),
                code=row["account_code"],
                name=row["name"],
                account_type=row["account_type"],
            )
            for row in rows
        ]

        return CoaAccountsResponse(success=True, accounts=accounts)
    except Exception as e:
        logger.error(f"Error fetching COGS accounts: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if conn:
            await conn.close()


@router.post("/items/{item_id}/stock-adjustment")
async def create_stock_adjustment(request: Request, item_id: str, response: Response = None):
    """Penyesuaian stok CEPAT satu barang -- disatukan ke penulis DOKUMEN penyesuaian (penyesuaian-3, putusan pemilik 12 Nov): per gudang (bawaan UTAMA),
    harga pokok rata-rata, bernomor SA-, pratinjau + batal lewat /api/stock-adjustments; data lama QSA- tak diubah. Badan lama tetap diterima
    (adjustment_type|type set/add/subtract, quantity, reason, reference_number, date, notes, adjustment_account_id|counter_account_id) + warehouse_id opsional.
    SATU transaksi: draf + posting lewat SAR._buat_draf / SAR._tulis_posting (penulis yang sama dengan POST /stock-adjustments + /post).
    Pagar yang dipertahankan: stok sesudah < 0 di gudang itu ditolak 422 STOK_TIDAK_CUKUP; barang tanpa stok (non-inventory) ditolak 422 BARANG_TANPA_STOK."""
    from decimal import Decimal
    import uuid as uuid_module
    from datetime import date as _date
    from . import stock_adjustments as SAR
    from ..schemas.stock_adjustments import CreateStockAdjustmentRequest
    from ..services import idem_buat
    from ..services.galat_berkode import GalatBerkode
    try:
        ctx = SAR.get_user_context(request)
        tid = ctx["tenant_id"]
        body = await request.json()
        if not isinstance(body, dict):
            raise GalatBerkode(422, "BADAN_TAK_SAH", "Badan permintaan harus berupa objek JSON.")

        # tipe lama (adjustment_type | type) + alias increase/decrease -> add/subtract
        tipe = body.get("adjustment_type") or body.get("type", "set")
        tipe = {"increase": "add", "decrease": "subtract"}.get(tipe, tipe)
        if tipe not in ("set", "add", "subtract"):
            raise GalatBerkode(422, "TIPE_TAK_SAH", "Jenis penyesuaian harus: set, add, atau subtract.", field="adjustment_type")
        if body.get("quantity") is None:
            raise GalatBerkode(422, "JUMLAH_WAJIB", "Jumlah wajib diisi.", field="quantity")
        try:
            jumlah = Decimal(str(body["quantity"]))
        except (TypeError, ValueError, ArithmeticError):
            raise GalatBerkode(422, "JUMLAH_TAK_SAH", "Jumlah harus berupa angka.", field="quantity")
        if jumlah <= 0:
            raise GalatBerkode(422, "JUMLAH_HARUS_POSITIF", "Jumlah harus lebih dari 0.", field="quantity")
        alasan = body.get("reason")
        if not alasan:
            raise GalatBerkode(422, "ALASAN_WAJIB", "Alasan wajib diisi.", field="reason")
        try:
            pid = uuid_module.UUID(item_id)
        except ValueError:
            raise GalatBerkode(400, "ID_BARANG_TAK_SAH", "Format ID barang tidak valid.")
        # akun lawan: kedua nama (FE form lama mengirim adjustment_account_id; penyesuaian-2 memakai counter_account_id); divalidasi SS.akun_lawan
        lawan = body.get("counter_account_id") or body.get("adjustment_account_id") or None

        pool = await get_pool()
        await SAR._ensure_stock_adj_role_preconditions(pool, tid)
        async with pool.acquire() as conn:
            async with conn.transaction():
                tgl_badan = body.get("date")
                tgl = _date.fromisoformat(str(tgl_badan)[:10]) if tgl_badan else await tanggal_dokumen(conn, tid)  # t10-tanggal-bisnis
                _kp, _sd, _lama = await idem_buat.mulai_aksi(conn, ctx, idem_buat.kunci_dari(request), "ITEM_STOCK_ADJ", item_id, body, response)
                if _lama is not None:
                    return _lama
                g = await SS_PEN.gudang(conn, tid, body.get("warehouse_id"), BlokPen(False))
                info = await SAR.get_product_info(conn, tid, pid)
                if not info:
                    raise GalatBerkode(404, "BARANG_TIDAK_DITEMUKAN", "Barang tidak ditemukan.")
                tipe_brg = await conn.fetchrow("SELECT item_type, track_inventory FROM products WHERE id = $1 AND tenant_id = $2", pid, tid)
                if not (tipe_brg and tipe_brg["item_type"] == "goods" and (tipe_brg["track_inventory"] if tipe_brg["track_inventory"] is not None else True)):
                    raise GalatBerkode(422, "BARANG_TANPA_STOK", f"{info['name']} tidak melacak stok (non-inventory); penyesuaian stok tidak berlaku.")
                # Law 13: kunci per (barang, gudang) SEBELUM membaca stok (anti-oversell lintas dokumen), lalu kunci buat per barang
                await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", f"STOCK:{item_id}:{g['id']}")
                await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", f"STOCK_ADJ_CREATE:{item_id}")
                sebelum = await SS_PEN.stok_gudang(conn, tid, pid, g["id"])   # Σ ledger gudang itu (Law 16)
                ubah = (jumlah - sebelum) if tipe == "set" else (jumlah if tipe == "add" else -jumlah)
                if ubah == 0:
                    return {"success": True, "message": "Stock unchanged", "old_stock": str(sebelum), "new_stock": str(sebelum), "adjustment": 0}
                if sebelum + ubah < 0:
                    raise GalatBerkode(422, "STOK_TIDAK_CUKUP", f"Stok {info['name']} di {g['name']} tidak cukup: tersedia {sebelum}, dikurangi {abs(ubah)}.",
                                       field="quantity")
                catatan = " - ".join(x for x in (str(alasan), body.get("notes")) if x)
                req = CreateStockAdjustmentRequest(
                    adjustment_date=tgl, adjustment_type="recount" if tipe == "set" else ("increase" if ubah > 0 else "decrease"),
                    warehouse_id=str(g["id"]), reference_no=body.get("reference_number") or body.get("reference") or None, notes=catatan,
                    items=[{"product_id": str(pid), "quantity_adjustment": ubah, "reason_detail": str(alasan),
                            "physical_quantity": jumlah if tipe == "set" else None}])
                r_buat = await SS_PEN.rencana_buat(conn, tid, req, BlokPen(False))
                sa_id, sa_number, total_nilai = await SAR._buat_draf(conn, ctx, req, r_buat)
                r_post = await SS_PEN.rencana_posting(conn, tid, sa_id, BlokPen(False), kunci=True, counter_account_id=lawan)
                journal_id, journal_number = await SAR._tulis_posting(conn, ctx, sa_id, r_post, tanpa_jurnal_bila_nol=True)
                harga = r_buat["baris_draf"][0]["unit_cost"]
                biaya_ledger = await conn.fetchval(
                    "SELECT average_cost FROM inventory_ledger WHERE tenant_id = $1 AND product_id = $2 AND average_cost IS NOT NULL AND average_cost > 0 "
                    "ORDER BY created_at DESC LIMIT 1", tid, pid)
                hasil = {
                    "success": True, "message": "Stock adjusted", "old_stock": str(sebelum), "new_stock": str(sebelum + ubah), "adjustment": str(ubah),
                    "journal_id": str(journal_id) if journal_id else None, "unit_cost": str(harga),
                    "cost_source": "wac" if biaya_ledger else ("purchase_price" if harga else None),
                    "adjustment_id": str(sa_id), "adjustment_number": sa_number, "journal_number": journal_number,
                    "warehouse_id": str(g["id"]), "warehouse_name": g["name"],
                }
                if not journal_id:
                    hasil["journal_note"] = "Jurnal tidak dibuat karena barang belum memiliki harga pokok (harga rata-rata/harga beli)"
                logger.info(f"Stock adjusted {item_id}: {sebelum} -> {sebelum + ubah} ({sa_number})")
                return await idem_buat.simpan(conn, ctx, _kp, _sd, "ITEM_STOCK_ADJ", hasil, sa_id)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error adjusting stock: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Gagal menyesuaikan stok.")


# =============================================================================
# ITEMS JOURNAL ENTRIES
# =============================================================================


@router.get("/items/{item_id}/journal-entries")
async def get_item_journal_entries(
    request: Request,
    item_id: str,
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
):
    """Get journal entries related to an item."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()
        offset = (page - 1) * limit

        async with pool.acquire() as conn:
            # Verify item exists
            item = await conn.fetchrow(
                "SELECT id, nama_produk as name FROM products WHERE id = $1 AND tenant_id = $2",
                item_id,
                ctx["tenant_id"],
            )

            if not item:
                raise HTTPException(status_code=404, detail="Item not found")

            # Get journal entries that reference this item
            # Traces via: inventory_ledger, sales invoices, purchase bills, and journal_lines.item_id
            entries = await conn.fetch(
                """
                SELECT DISTINCT
                    je.id,
                    je.journal_number,
                    je.journal_date,
                    je.description,
                    je.created_at
                FROM journal_entries je
                WHERE je.tenant_id = $1
                    AND je.status = 'POSTED'
                    AND (
                        je.id IN (
                            SELECT il.journal_id FROM inventory_ledger il
                            WHERE il.product_id = $2 AND il.tenant_id = $1 AND il.journal_id IS NOT NULL
                        )
                        OR
                        (je.source_type IN ('INVOICE', 'SALES_INVOICE', 'SALES_INVOICE_COGS') AND je.source_id::text IN (
                            SELECT si.id::text FROM sales_invoices si
                            JOIN sales_invoice_items sii ON sii.invoice_id = si.id
                            WHERE sii.item_id = $2::uuid
                        ))
                        OR
                        (je.source_type IN ('BILL', 'PURCHASE_INVOICE') AND je.source_id::text IN (
                            SELECT b.id::text FROM bills b
                            JOIN bill_items bi ON bi.bill_id = b.id
                            WHERE bi.product_id = $2::uuid
                        ))
                        OR
                        je.id IN (
                            SELECT jl.journal_id FROM journal_lines jl
                            WHERE jl.item_id = $2::uuid
                        )
                    )
                ORDER BY je.journal_date DESC, je.created_at DESC
                LIMIT $3 OFFSET $4
                """,
                ctx["tenant_id"],
                item_id,
                limit,
                offset,
            )

            # Get total count
            total = await conn.fetchval(
                """
                SELECT COUNT(DISTINCT je.id)
                FROM journal_entries je
                WHERE je.tenant_id = $1
                    AND je.status = 'POSTED'
                    AND (
                        je.id IN (
                            SELECT il.journal_id FROM inventory_ledger il
                            WHERE il.product_id = $2 AND il.tenant_id = $1 AND il.journal_id IS NOT NULL
                        )
                        OR
                        (je.source_type IN ('INVOICE', 'SALES_INVOICE', 'SALES_INVOICE_COGS') AND je.source_id::text IN (
                            SELECT si.id::text FROM sales_invoices si
                            JOIN sales_invoice_items sii ON sii.invoice_id = si.id
                            WHERE sii.item_id = $2::uuid
                        ))
                        OR
                        (je.source_type IN ('BILL', 'PURCHASE_INVOICE') AND je.source_id::text IN (
                            SELECT b.id::text FROM bills b
                            JOIN bill_items bi ON bi.bill_id = b.id
                            WHERE bi.product_id = $2::uuid
                        ))
                        OR
                        je.id IN (
                            SELECT jl.journal_id FROM journal_lines jl
                            WHERE jl.item_id = $2::uuid
                        )
                    )
                """,
                ctx["tenant_id"],
                item_id,
            )

            # Build response with lines for each entry
            result_entries = []
            for entry in entries:
                # Get lines for this journal entry
                lines = await conn.fetch(
                    """
                    SELECT
                        jl.line_number,
                        a.account_code,
                        a.name as account_name,
                        jl.memo,
                        jl.debit,
                        jl.credit
                    FROM journal_lines jl
                    INNER JOIN chart_of_accounts a ON a.id = jl.account_id
                    WHERE jl.journal_id = $1
                    ORDER BY jl.line_number
                    """,
                    entry["id"],
                )

                result_entries.append(
                    {
                        "id": str(entry["id"]),
                        "date": entry["journal_date"].isoformat(),
                        "reference": entry["journal_number"],
                        "description": entry["description"],
                        "lines": [
                            {
                                "accountName": f"{line['account_code']} - {line['account_name']}",
                                "debit": int(line["debit"]),
                                "credit": int(line["credit"]),
                            }
                            for line in lines
                        ],
                    }
                )

            return {"success": True, "entries": result_entries, "total": total or 0}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting journal entries: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get journal entries")


# =============================================================================
# SINGLE ITEM STOCK TRANSFER
# =============================================================================


@router.post("/items/{item_id}/stock-transfer")
async def create_single_item_stock_transfer(request: Request, item_id: str):
    """Pindah stok SATU barang antargudang (jalan pintas). 12 Okt 2026: mendelegasikan ke inti Pemindahan Barang
    (services/pemindahan_barang): nilai = WAC server, TANPA jurnal, kartu RELOKASI_GUDANG + audit. Tanpa flag
    `pemindahan_barang` -> 409. Jalur lama menulis purchase_price ke average_cost (mencemari WAC penjualan berikutnya)
    dan memakai source_type STOCK_TRANSFER di luar penjaga Check 21.

    Body: from_warehouse_id, to_warehouse_id, quantity (>0), notes (opsional).
    """
    from ..services import pemindahan_barang as pb

    try:
        ctx = get_user_context(request)
        pool = await get_pool()
        body = await request.json()
        from_warehouse_id = body.get("from_warehouse_id")
        to_warehouse_id = body.get("to_warehouse_id")
        quantity = body.get("quantity")
        notes = body.get("notes")
        if not from_warehouse_id:
            raise HTTPException(status_code=400, detail="from_warehouse_id is required")
        if not to_warehouse_id:
            raise HTTPException(status_code=400, detail="to_warehouse_id is required")
        if not quantity or quantity <= 0:
            raise HTTPException(status_code=400, detail="quantity must be greater than 0")
        if from_warehouse_id == to_warehouse_id:
            raise HTTPException(status_code=400, detail="Source and destination warehouse must be different")
        try:
            data = pb.PemindahanIn(
                from_warehouse_id=from_warehouse_id, to_warehouse_id=to_warehouse_id, notes=notes,
                items=[pb.BarangIn(item_id=item_id, quantity_requested=Decimal(str(quantity)), notes=notes)])
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid item or warehouse id")
        async with pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute("SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"])
                await pb.pastikan_aktif(conn, ctx["tenant_id"])
                transfer_id = await pb.buat_draf(conn, ctx, data)
                await pb.selesaikan(conn, ctx, transfer_id)
                r = await conn.fetchrow(
                    """SELECT st.transfer_number, fw.name AS dari, tw.name AS ke, i.item_name, i.unit
                       FROM stock_transfers st JOIN stock_transfer_items i ON i.stock_transfer_id = st.id
                       JOIN warehouses fw ON fw.id = st.from_warehouse_id JOIN warehouses tw ON tw.id = st.to_warehouse_id
                       WHERE st.id = $1 AND st.tenant_id = $2""", transfer_id, ctx["tenant_id"])
                return {
                    "success": True,
                    "message": f"Transfer berhasil: {quantity} {r['unit'] or ''} dipindahkan".replace("  ", " "),
                    "data": {
                        "transfer_id": str(transfer_id), "transfer_number": r["transfer_number"], "item_id": item_id,
                        "item_name": r["item_name"], "from_warehouse": r["dari"], "to_warehouse": r["ke"],
                        "quantity": quantity, "unit": r["unit"],
                    },
                }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating stock transfer: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to create stock transfer")
