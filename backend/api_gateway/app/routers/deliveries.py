"""
Deliveries Router - Pengiriman Barang

Read-only endpoints for delivery management (invoice_fulfillments wrapper).
Create still uses POST /api/sales-invoices/{id}/fulfill.
"""

from fastapi import APIRouter, HTTPException, Request, Query
from typing import Optional, Literal
from datetime import date
import logging
from decimal import Decimal
from io import BytesIO
from fastapi.responses import StreamingResponse
from ..services.pdf_service import get_pdf_service
from ..services import pengiriman_serah as _serah
from ..services.so_riwayat import catat_riwayat

logger = logging.getLogger(__name__)
router = APIRouter()


async def get_pool():
    from ..services.db_pool import get_db_pool

    return await get_db_pool()


def get_user_context(request: Request) -> dict:
    if not hasattr(request.state, "user") or not request.state.user:
        raise HTTPException(status_code=401, detail="Perlu masuk (login).")
    user = request.state.user
    tenant_id = user.get("tenant_id")
    if not tenant_id:
        raise HTTPException(status_code=401, detail="Konteks pengguna tidak sah.")
    return {"tenant_id": tenant_id, "user_id": user.get("user_id")}


def _syarat_cari(search, mulai: int) -> tuple:
    """(predikat, [parameter]) ?search= -- SATU sumber untuk daftar dan /summary?search= (alias f, si, c; $1 tenant)."""
    from ..services.kode_order import sql_cari_so_induk
    if not isinstance(search, str) or not search:
        return None, []
    p = f"${mulai}"
    return (f"(f.fulfillment_number ILIKE {p} OR c.nama ILIKE {p} OR si.invoice_number ILIKE {p} OR c.search_text ILIKE {p} OR "
            + sql_cari_so_induk("delivery", "f", "$1", p) + ")"), [f"%{search}%"]


_FROM_GABUNG = """FROM invoice_fulfillments f
            LEFT JOIN sales_invoices si ON si.id = f.invoice_id
            LEFT JOIN sales_orders so ON so.id = COALESCE(f.sales_order_id, si.sales_order_id) AND so.tenant_id = f.tenant_id
            LEFT JOIN customers c ON c.id = COALESCE(si.customer_id, so.customer_id)"""


@router.get("/summary")
async def get_deliveries_summary(request: Request, search: Optional[str] = Query(None)):
    """Summary counts for Pengiriman Barang dashboard.

    20 Okt 2026 (PENJUALAN-SERAGAM): + perlu_resi_count / belum_dicatat_count (pill aksi, services/pengiriman_daftar; 0 bila fitur serah
    OFF) dan ?search= (predikat daftar, _syarat_cari; membatasi SEMUA hitungan). Tanpa search = hitungan lama dari tabel apa adanya."""
    from ..services import pengiriman_daftar as _pg
    ctx = get_user_context(request)
    pool = await get_pool()
    _cari, _par = _syarat_cari(search, 2)

    async with pool.acquire() as conn:
        await conn.execute(f"SET LOCAL app.tenant_id = '{ctx['tenant_id']}'")
        if _cari:
            row = await conn.fetchrow(
                f"""
                SELECT
                    COUNT(*) FILTER (WHERE f.status = 'posted') AS total_posted,
                    COUNT(*) FILTER (WHERE f.status = 'voided') AS total_voided,
                    COUNT(*) FILTER (WHERE f.fulfillment_date = tanggal_bisnis($1)) AS today_count,
                    COUNT(*) AS total_all
                {_FROM_GABUNG}
                WHERE f.tenant_id = $1 AND {_cari}
                """,
                ctx["tenant_id"], *_par,
            )
        else:
            row = await conn.fetchrow(
                """
                SELECT
                    COUNT(*) FILTER (WHERE status = 'posted') AS total_posted,
                    COUNT(*) FILTER (WHERE status = 'voided') AS total_voided,
                    COUNT(*) FILTER (WHERE fulfillment_date = tanggal_bisnis($1)) AS today_count,  -- kelompok C 26 Sep: hari ini = tanggal bisnis tenant
                    COUNT(*) AS total_all
                FROM invoice_fulfillments
                WHERE tenant_id = $1
                """,
                ctx["tenant_id"],
            )
        aksi = {k: 0 for k in _pg.AKSI}
        if await _serah.aktif(conn, ctx["tenant_id"]):
            a = await conn.fetchrow(
                "SELECT " + ", ".join(f"COUNT(*) FILTER (WHERE {_pg.sql_aksi(k)}) AS {k}" for k in _pg.AKSI)
                + f" {_FROM_GABUNG if _cari else 'FROM invoice_fulfillments f'} WHERE f.tenant_id = $1" + (f" AND {_cari}" if _cari else ""),
                ctx["tenant_id"], *_par)
            aksi = {k: a[k] or 0 for k in _pg.AKSI}
    return {
        "total_posted": row["total_posted"],
        "total_voided": row["total_voided"],
        "today_count": row["today_count"],
        "total_all": row["total_all"],
        **{f"{k}_count": v for k, v in aksi.items()},
    }


@router.get("")
async def list_deliveries(
    request: Request,
    # Kosakata DISEBUT, bukan diam. Sebelum ini `status` adalah str polos, jadi
    # nilai asing dijawab 200 `total:0` -- pemanggil tak punya cara membedakan
    # "tidak ada data" dari "parameter tidak dimengerti".
    status: Optional[Literal["posted", "voided"]] = Query(None),
    customer_id: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    date_from: Optional[date] = Query(None),  # U9: filter tanggal kirim (aditif)
    date_to: Optional[date] = Query(None),
    sort_by: str = Query("delivery_date"),
    sort_order: str = Query("desc"),
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    # 20 Okt 2026 (PENJUALAN-SERAGAM, aditif): skip/offset di samping page (prioritas offset > page > skip) + has_more di respons
    skip: int = Query(0, ge=0),
    offset: int = Query(0, ge=0),
    aksi: Optional[Literal["perlu_resi", "belum_dicatat"]] = Query(None),
):
    """List deliveries with pagination, filters, and sorting."""
    ctx = get_user_context(request)
    pool = await get_pool()

    sort_map = {
        "delivery_date": "f.fulfillment_date",
        "delivery_number": "f.fulfillment_number",
        "customer_name": "c.nama",
        "status": "f.status",
        "created_at": "f.created_at",
    }
    db_sort = sort_map.get(sort_by, "f.fulfillment_date")
    direction = "ASC" if sort_order.lower() == "asc" else "DESC"

    conditions = ["f.tenant_id = $1"]
    params = [ctx["tenant_id"]]
    idx = 2

    if status:
        conditions.append(f"f.status = ${idx}")
        # JANGAN .upper(): penulisnya menulis huruf KECIL -- sales_invoices.py
        # menulis 'posted' saat fulfill dan 'voided' saat void, dan ringkasan di
        # atas membacanya huruf kecil juga. .upper() memetakan SETIAP masukan ke
        # huruf besar, yang tak pernah cocok dengan baris mana pun; penyaring ini
        # mengembalikan 0 untuk apa pun, termasuk nilai yang sah.
        params.append(status)
        idx += 1

    if customer_id:
        conditions.append(f"COALESCE(si.customer_id, so.customer_id) = ${idx}")
        params.append(customer_id)
        idx += 1

    if search:
        _pred_cari, _par_cari = _syarat_cari(search, idx)  # SATU sumber dgn /summary?search=
        conditions.append(_pred_cari)
        params.extend(_par_cari)
        idx += 1

    if date_from:
        conditions.append(f"f.fulfillment_date >= ${idx}")
        params.append(date_from)
        idx += 1

    if date_to:
        conditions.append(f"f.fulfillment_date <= ${idx}")
        params.append(date_to)
        idx += 1

    from ..services import pengiriman_daftar as _pg
    async with pool.acquire() as _c0:
        _serah_aktif = await _serah.aktif(_c0, ctx["tenant_id"])
    if aksi in _pg.AKSI:  # panggilan langsung tanpa aksi = objek Query -> mati. Fitur serah OFF -> pill kosong (bukan semua baris)
        conditions.append(_pg.sql_aksi(aksi) if _serah_aktif else "FALSE")
    where_clause = " AND ".join(conditions)
    _i = lambda v, d: v if isinstance(v, int) else d  # noqa: E731  (panggilan langsung tanpa argumen = objek Query)
    _offset, _page, _skip = _i(offset, 0), _i(page, 1), _i(skip, 0)
    offset = _offset if _offset > 0 else ((_page - 1) * per_page if _page > 1 else _skip)

    async with pool.acquire() as conn:
        await conn.execute(f"SET LOCAL app.tenant_id = '{ctx['tenant_id']}'")

        total_row = await conn.fetchrow(
            f"""
            SELECT COUNT(*) AS total
            FROM invoice_fulfillments f
            LEFT JOIN sales_invoices si ON si.id = f.invoice_id
            LEFT JOIN sales_orders so ON so.id = COALESCE(f.sales_order_id, si.sales_order_id) AND so.tenant_id = f.tenant_id
            LEFT JOIN customers c ON c.id = COALESCE(si.customer_id, so.customer_id)
            LEFT JOIN warehouses w ON w.id = f.warehouse_id
            WHERE {where_clause}
            """,
            *params,
        )
        total = total_row["total"]

        rows = await conn.fetch(
            f"""
            SELECT
                f.id,
                f.fulfillment_number AS delivery_number,
                f.fulfillment_date AS delivery_date,
                f.status,
                f.notes,
                f.created_at,
                f.posted_at,
                f.voided_at,
                f.voided_reason,
                f.asal AS asal,
                si.id AS invoice_id,
                si.invoice_number,
                COALESCE(si.customer_id, so.customer_id) AS customer_id,
                COALESCE(f.sales_order_id, si.sales_order_id) AS sales_order_id,
                so.order_number AS sales_order_number,
                c.nama AS customer_name,
                c.telepon AS customer_phone,
                c.alamat AS customer_address,
                w.name AS warehouse_name,
                (SELECT COUNT(*) FROM invoice_fulfillment_items fi WHERE fi.fulfillment_id = f.id) AS item_count,
                (SELECT COALESCE(SUM(fi.total_cost), 0) FROM invoice_fulfillment_items fi WHERE fi.fulfillment_id = f.id) AS total_cogs
            FROM invoice_fulfillments f
            LEFT JOIN sales_invoices si ON si.id = f.invoice_id
            LEFT JOIN sales_orders so ON so.id = COALESCE(f.sales_order_id, si.sales_order_id) AND so.tenant_id = f.tenant_id
            LEFT JOIN customers c ON c.id = COALESCE(si.customer_id, so.customer_id)
            LEFT JOIN warehouses w ON w.id = f.warehouse_id
            WHERE {where_clause}
            ORDER BY {db_sort} {direction}, f.id
            LIMIT ${idx} OFFSET ${idx + 1}
            """,
            *params,
            per_page,
            offset,
        )

    items = [
        {
            "id": str(r["id"]),
            "delivery_number": r["delivery_number"],
            "delivery_date": r["delivery_date"].isoformat()
            if r["delivery_date"]
            else None,
            "status": r["status"],
            "notes": r["notes"],
            "created_at": r["created_at"].isoformat() if r["created_at"] else None,
            "posted_at": r["posted_at"].isoformat() if r["posted_at"] else None,
            "voided_at": r["voided_at"].isoformat() if r["voided_at"] else None,
            "voided_reason": r["voided_reason"],
            "asal": r["asal"],
            "invoice_id": str(r["invoice_id"]) if r["invoice_id"] else None,
            "invoice_number": r["invoice_number"],
            "customer_id": str(r["customer_id"]) if r["customer_id"] else None,
            "sales_order_id": str(r["sales_order_id"]) if r["sales_order_id"] else None,
            "sales_order_number": r["sales_order_number"],
            "customer_name": r["customer_name"],
            "customer_phone": r["customer_phone"],
            "customer_address": r["customer_address"],
            "warehouse_name": r["warehouse_name"],
            "item_count": r["item_count"],
            "total_cogs": float(r["total_cogs"]) if r["total_cogs"] else 0,
        }
        for r in rows
    ]

    from ..services.kode_order import tempel_kode as _tempel_kode
    async with pool.acquire() as _c:
        await _tempel_kode(_c, ctx["tenant_id"], "delivery", items)
        if items and _serah_aktif:
            _m = await _serah.muat(_c, ctx["tenant_id"], [i["id"] for i in items])
            for i in items:
                i["serah"] = _m[i["id"]]
        # 20 Okt 2026 (PENJUALAN-SERAGAM): serah_keadaan/serah_teks/aksi (pill) + documents[]/doc_count (= bundel tanpa rekap, satu definisi)
        import uuid as _uuid
        _fs = await _pg.fakta_serah(_c, ctx["tenant_id"], [r["id"] for r in rows]) if _serah_aktif else {}
        _kel = await _pg.kelompok_untuk(_c, ctx["tenant_id"], [{"id": r["id"], "invoice_id": r["invoice_id"], "sales_order_id": r["sales_order_id"]} for r in rows])
        for i, r in zip(items, rows):
            i.update(_pg.serah_fakta(_serah_aktif, r["status"], *_fs.get(r["id"], (None, None, 0))))
            i["documents"] = _pg.documents_baris(_kel.get(r["id"], []))
            i["doc_count"] = len(i["documents"])
    return {
        "items": items,
        "total": total,
        "has_more": (offset + per_page) < (total or 0),
        "page": page,
        "per_page": per_page,
        "total_pages": (total + per_page - 1) // per_page if total else 0,
    }


@router.get("/{delivery_id}")
async def get_delivery_detail(delivery_id: str, request: Request):
    """Full delivery detail with items and journals."""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute(f"SET LOCAL app.tenant_id = '{ctx['tenant_id']}'")

        row = await conn.fetchrow(
            """
            SELECT
                f.id,
                f.fulfillment_number AS delivery_number,
                f.fulfillment_date AS delivery_date,
                f.status,
                f.notes,
                f.created_at,
                f.updated_at,
                f.posted_at,
                f.voided_at,
                f.voided_reason,
                f.journal_id,
                f.revenue_journal_id,
                f.asal AS asal,
                si.id AS invoice_id,
                si.invoice_number,
                COALESCE(si.customer_id, so.customer_id) AS customer_id,
                COALESCE(f.sales_order_id, si.sales_order_id) AS sales_order_id,
                so.order_number AS sales_order_number,
                c.nama AS customer_name,
                c.telepon AS customer_phone,
                c.alamat AS customer_address,
                w.name AS warehouse_name,
                (SELECT COUNT(*) FROM invoice_fulfillment_items fi WHERE fi.fulfillment_id = f.id) AS item_count,
                (SELECT COALESCE(SUM(fi.total_cost), 0) FROM invoice_fulfillment_items fi WHERE fi.fulfillment_id = f.id) AS total_cogs
            FROM invoice_fulfillments f
            LEFT JOIN sales_invoices si ON si.id = f.invoice_id
            LEFT JOIN sales_orders so ON so.id = COALESCE(f.sales_order_id, si.sales_order_id) AND so.tenant_id = f.tenant_id
            LEFT JOIN customers c ON c.id = COALESCE(si.customer_id, so.customer_id)
            LEFT JOIN warehouses w ON w.id = f.warehouse_id
            WHERE f.id = $1 AND f.tenant_id = $2
            """,
            delivery_id,
            ctx["tenant_id"],
        )

        if not row:
            raise HTTPException(status_code=404, detail="Surat Jalan tidak ditemukan.")

        items_rows = await conn.fetch(
            """
            SELECT
                fi.id,
                fi.invoice_item_id,
                fi.product_id,
                fi.quantity,
                fi.unit_cost,
                fi.total_cost,
                fi.notes,
                fi.created_at,
                p.nama_produk AS product_name,
                COALESCE(sii.description, soi.description) AS line_description,
                p.sku AS product_sku,
                COALESCE(sii.unit, soi.unit) AS unit
            FROM invoice_fulfillment_items fi
            LEFT JOIN products p ON p.id = fi.product_id
            LEFT JOIN sales_invoice_items sii ON sii.id = fi.invoice_item_id
            LEFT JOIN sales_order_items soi ON soi.id = fi.sales_order_item_id
            WHERE fi.fulfillment_id = $1
            ORDER BY fi.created_at
            """,
            delivery_id,
        )

        journal_ids = [row["journal_id"], row["revenue_journal_id"]]
        journal_ids = [jid for jid in journal_ids if jid is not None]

        if journal_ids:
            journals_rows = await conn.fetch(
                """
                SELECT
                    je.id AS journal_id,
                    je.journal_number,
                    je.journal_date,
                    je.description,
                    je.source_type,
                    je.total_debit,
                    je.total_credit,
                    je.status,
                    json_agg(
                        json_build_object(
                            'account_code', coa.account_code,
                            'account_name', coa.name,
                            'debit', jl.debit,
                            'credit', jl.credit,
                            'description', jl.memo
                        ) ORDER BY jl.line_number
                    ) AS lines
                FROM journal_entries je
                LEFT JOIN journal_lines jl ON jl.journal_id = je.id
                LEFT JOIN chart_of_accounts coa ON coa.id = jl.account_id
                WHERE je.id = ANY($1::uuid[])
                GROUP BY je.id, je.journal_number, je.journal_date, je.description,
                         je.source_type, je.total_debit, je.total_credit, je.status
                ORDER BY je.journal_date
                """,
                journal_ids,
            )
        else:
            journals_rows = []

    detail = {
        "id": str(row["id"]),
        "delivery_number": row["delivery_number"],
        "delivery_date": row["delivery_date"].isoformat()
        if row["delivery_date"]
        else None,
        "status": row["status"],
        "notes": row["notes"],
        "created_at": row["created_at"].isoformat() if row["created_at"] else None,
        "updated_at": row["updated_at"].isoformat() if row["updated_at"] else None,
        "posted_at": row["posted_at"].isoformat() if row["posted_at"] else None,
        "voided_at": row["voided_at"].isoformat() if row["voided_at"] else None,
        "voided_reason": row["voided_reason"],
        "journal_id": str(row["journal_id"]) if row["journal_id"] else None,
        "revenue_journal_id": str(row["revenue_journal_id"])
        if row["revenue_journal_id"]
        else None,
        "sales_order_id": str(row["sales_order_id"]) if row["sales_order_id"] else None,
        "sales_order_number": row["sales_order_number"],
        "asal": row["asal"],
        "invoice_id": str(row["invoice_id"]) if row["invoice_id"] else None,
        "invoice_number": row["invoice_number"],
        "customer_id": str(row["customer_id"]) if row["customer_id"] else None,
        "customer_name": row["customer_name"],
        "customer_phone": row["customer_phone"],
        "customer_address": row["customer_address"],
        "warehouse_name": row["warehouse_name"],
        "item_count": row["item_count"],
        "total_cogs": float(row["total_cogs"]) if row["total_cogs"] else 0,
        "items": [
            {
                "id": str(i["id"]),
                "invoice_item_id": str(i["invoice_item_id"]),
                "product_id": str(i["product_id"]) if i["product_id"] else None,
                "product_name": i["product_name"],
                "description": i["line_description"],
                "product_sku": i["product_sku"],
                "quantity": str(i["quantity"]),
                "unit": i["unit"],
                "unit_cost": str(i["unit_cost"])
                if i["unit_cost"] is not None
                else None,
                "total_cost": str(i["total_cost"])
                if i["total_cost"] is not None
                else None,
                "notes": i["notes"],
            }
            for i in items_rows
        ],
        "journals": [
            {
                "journal_id": str(j["journal_id"]),
                "entry_number": j["journal_number"],
                "entry_date": j["journal_date"].isoformat()
                if j["journal_date"]
                else None,
                "description": j["description"],
                "source_type": j["source_type"],
                "total_debit": str(j["total_debit"])
                if j["total_debit"] is not None
                else None,
                "total_credit": str(j["total_credit"])
                if j["total_credit"] is not None
                else None,
                "status": j["status"],
                "lines": j["lines"],
            }
            for j in journals_rows
        ],
    }

    from ..services.kode_order import tempel_kode as _tempel_kode
    async with pool.acquire() as _c:
        await _tempel_kode(_c, ctx["tenant_id"], "delivery", [detail])
        detail.update(await sisa_faktur_sumber(_c, ctx["tenant_id"], row["invoice_id"])
                      if row["invoice_id"] else {"invoice_total": None, "invoice_outstanding": None})
        _aktif = await _serah.aktif(_c, ctx["tenant_id"])
        if _aktif:  # tanpa flag: respons TAK membawa kunci serah (byte-identik)
            detail["serah"] = (await _serah.muat(_c, ctx["tenant_id"], [row["id"]]))[str(row["id"])]
        from ..services import pengiriman_daftar as _pg  # 20 Okt 2026: keadaan serah + aksi (aditif; fungsi SAMA dgn daftar)
        _fs = await _pg.fakta_serah(_c, ctx["tenant_id"], [row["id"]]) if _aktif else {}
        detail.update(_pg.serah_fakta(_aktif, row["status"], *_fs.get(row["id"], (None, None, 0))))
    return detail


async def sisa_faktur_sumber(conn, tenant_id: str, invoice_id) -> dict:
    """5 Okt 2026 (WORKSPACE D-standar, langkah "Pelunasan faktur"): nilai + sisa faktur sumber surat jalan.
    Sisa = journal-derived compute_ar_outstanding (Law 1/16); faktur lunas tak punya baris di sana -> "0.00".
    Faktur draf/void -> keduanya null (FE menyembunyikan langkah)."""
    f = await conn.fetchrow("SELECT status, total_amount FROM sales_invoices WHERE id = $1 AND tenant_id = $2",
                            invoice_id, tenant_id)
    if not f or f["status"] in ("draft", "void", "voided"):
        return {"invoice_total": None, "invoice_outstanding": None}
    sisa = await conn.fetchval("SELECT outstanding FROM compute_ar_outstanding($1) WHERE invoice_id = $2",
                               tenant_id, invoice_id)
    return {"invoice_total": f"{Decimal(str(f['total_amount'] or 0)):.2f}",
            "invoice_outstanding": f"{Decimal(str(sisa if sisa is not None else 0)):.2f}"}


# =============================================================================
# GENERATE PDF — SURAT JALAN
# =============================================================================


async def muat_pdf_surat_jalan(conn, ctx, delivery_id: str) -> dict:
    """Konteks PDF surat jalan (P3 SO-dokumen: SATU sumber). Dipindah VERBATIM dari get_delivery_pdf.
    -> {delivery_data, row}.

    1 Okt 2026: `SET LOCAL app.tenant_id = '<f-string>'` DICABUT -- gateway = postgres BYPASSRLS (diukur: rolbypassrls t)
    dan di luar transaksi (no-op): bukan pagar, hanya permukaan injeksi. Pagar = filter tenant EKSPLISIT di tiap join
    ber-tenant (customers, warehouses, products) + item dari id SJ yang SUDAH lolos filter tenant (row["id"])."""

    row = await conn.fetchrow(
        """
        SELECT
            f.id,
            f.fulfillment_number AS delivery_number,
            f.fulfillment_date AS delivery_date,
            f.status,
            f.notes,
            f.asal AS asal,
            si.id AS invoice_id,
            si.invoice_number,
            COALESCE(si.customer_id, so.customer_id) AS customer_id,
            c.nama AS customer_name,
            c.telepon AS customer_phone,
            c.alamat AS customer_address,
            w.name AS warehouse_name,
            (SELECT COUNT(*) FROM invoice_fulfillment_items fi WHERE fi.fulfillment_id = f.id) AS item_count,
            (SELECT COALESCE(SUM(fi.total_cost), 0) FROM invoice_fulfillment_items fi WHERE fi.fulfillment_id = f.id) AS total_cogs
        FROM invoice_fulfillments f
        LEFT JOIN sales_invoices si ON si.id = f.invoice_id
        LEFT JOIN sales_orders so ON so.id = COALESCE(f.sales_order_id, si.sales_order_id) AND so.tenant_id = f.tenant_id
        LEFT JOIN customers c ON c.id = COALESCE(si.customer_id, so.customer_id) AND c.tenant_id = f.tenant_id
        LEFT JOIN warehouses w ON w.id = f.warehouse_id AND w.tenant_id = f.tenant_id
        WHERE f.id = $1 AND f.tenant_id = $2 AND (si.id IS NULL OR si.tenant_id = f.tenant_id)
        """,
        delivery_id,
        ctx["tenant_id"],
    )

    if not row:
        raise HTTPException(status_code=404, detail="Surat Jalan tidak ditemukan.")

    items_rows = await conn.fetch(
        """
        SELECT
            fi.id,
            fi.quantity,
            fi.notes,
            p.nama_produk AS product_name,
            COALESCE(sii.description, soi.description) AS line_description,
            p.sku AS product_sku,
            COALESCE(sii.unit, soi.unit) AS unit
        FROM invoice_fulfillment_items fi
        LEFT JOIN products p ON p.id = fi.product_id AND p.tenant_id = $2
        LEFT JOIN sales_invoice_items sii ON sii.id = fi.invoice_item_id
            LEFT JOIN sales_order_items soi ON soi.id = fi.sales_order_item_id
        WHERE fi.fulfillment_id = $1
        ORDER BY fi.created_at
        """,
        row["id"],
        ctx["tenant_id"],
    )

    # Fetch tenant info
    tenant_row = await conn.fetchrow(
        'SELECT display_name, address, phone, logo_url FROM "Tenant" WHERE id = $1',
        ctx["tenant_id"],
    )
    tenant_info = {
        "name": tenant_row["display_name"] if tenant_row else str(ctx["tenant_id"]),
        "address": tenant_row["address"] if tenant_row else None,
        "phone": tenant_row["phone"] if tenant_row else None,
    }
    # 1 Okt 2026: logo kanan atas seperti dokumen lain (kop yang sama dengan Rekap -- logo_url -> data URI).
    from ..services.rekap_pesanan import muat_kop as _muat_kop
    tenant_info["logo_data"] = (await _muat_kop(conn, ctx["tenant_id"]))["logo_data"]
    delivery_data = {
        "id": str(row["id"]),
        "delivery_number": row["delivery_number"],
        "delivery_date": row["delivery_date"].isoformat()
        if row["delivery_date"]
        else None,
        "status": row["status"],
        "notes": row["notes"],
        "asal": row["asal"],
        "invoice_id": str(row["invoice_id"]) if row["invoice_id"] else None,
        "invoice_number": row["invoice_number"],
        "customer_id": str(row["customer_id"]) if row["customer_id"] else None,
        "customer_name": row["customer_name"],
        "customer_phone": row["customer_phone"],
        "customer_address": row["customer_address"],
        "tenant": tenant_info,
        "items": [
            {
                "id": str(i["id"]),
                # Unit D: Surat Jalan mencetak keterangan BARIS FAKTUR (yang dicetak faktur),
                # bukan nama master; nama master hanya bila baris tak punya teks.
                "product_name": i["line_description"] or i["product_name"],
                "product_sku": i["product_sku"],
                "quantity": str(i["quantity"]),
                "unit": i["unit"],
                "notes": i["notes"],
            }
            for i in items_rows
        ],
    }
    # 12 Okt 2026 (putusan pemilik, pola SAP/NetSuite/Accurate): No. SPK/kode order = nomor INTERNAL produksi, TIDAK dicetak di dokumen konsumen.
    if await _serah.aktif(conn, ctx["tenant_id"]):  # blok "Serah-terima" di cetakan; tanpa flag cetakan TAK berubah
        _sr = (await _serah.muat(conn, ctx["tenant_id"], [row["id"]]))[str(row["id"])]
        if _sr["metode"] or _sr["kirim_ke"] or _sr["resi"]:
            delivery_data["serah"] = {**_sr, "warehouse_name": row["warehouse_name"]}
    return {"delivery_data": delivery_data, "row": row}


@router.get("/{delivery_id}/history")
async def get_delivery_history(delivery_id: str, request: Request, limit: int = Query(200, ge=1, le=500)):
    """Riwayat Surat Jalan, bentuk SAMA dengan GET /sales-orders/{id}/history (so_riwayat.riwayat_surat_jalan)."""
    import uuid as _uuid
    ctx = get_user_context(request)
    try:
        uid = _uuid.UUID(str(delivery_id))
    except ValueError:
        raise HTTPException(status_code=404, detail="Pengiriman tidak ditemukan")
    from ..services.dashboard_izin import boleh_baca
    from ..services.so_riwayat import riwayat_surat_jalan
    pool = await get_pool()
    async with pool.acquire() as conn:
        data = await riwayat_surat_jalan(conn, ctx["tenant_id"], uid, lambda m: boleh_baca(request, m), limit)
    if data is None:
        raise HTTPException(status_code=404, detail="Pengiriman tidak ditemukan")
    return {"success": True, "data": data}


@router.put("/{delivery_id}/serah")
async def ubah_serah_surat_jalan(delivery_id: str, request: Request, body: _serah.SerahIn):
    """Ganti-seluruhnya cara serah / alamat kirim / resi / tanggal diterima satu Surat Jalan (kontrak pengiriman serah).
    Metadata NON-keuangan: nol jurnal/stok/HPP, TIDAK meng-UPDATE invoice_fulfillments (trigger hitung-ulang SO tak
    terpancing). Periode tertutup BOLEH (K4, MASTER 11 Okt) -- jejaknya audit_logs di transaksi yang sama (Law 8/12).
    SJ voided -> 409. Kosong = tanpa cara serah (bukan bawaan tenant; bawaan hanya saat membuat)."""
    ctx = get_user_context(request)
    try:
        from uuid import UUID as _UUID
        fid = _UUID(delivery_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Surat Jalan tidak ditemukan.")
    tid = ctx["tenant_id"]
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.tenant_id', $1, true)", str(tid))
            await _serah.pastikan_aktif(conn, tid)
            await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", f"DELIVERY_SERAH:{tid}:{fid}")
            sj = await conn.fetchrow(
                """SELECT f.id, f.fulfillment_number, f.status, COALESCE(f.sales_order_id, si.sales_order_id) AS sales_order_id
                   FROM invoice_fulfillments f
                   LEFT JOIN sales_invoices si ON si.id = f.invoice_id AND si.tenant_id = f.tenant_id
                   WHERE f.id = $1 AND f.tenant_id = $2""", fid, tid)
            if not sj:
                raise HTTPException(status_code=404, detail="Surat Jalan tidak ditemukan.")
            if sj["status"] == "voided":
                raise HTTPException(status_code=409, detail={
                    "code": "SJ_VOID", "message": "Surat Jalan ini sudah dibatalkan, serah-terimanya tak bisa diubah."})
            lama = (await _serah.muat(conn, tid, [fid]))[str(fid)]
            s = await _serah.selesaikan(conn, tid, sj["sales_order_id"], body, pakai_bawaan=False)
            await _serah.tulis(conn, tid, ctx["user_id"], fid, s)
            baru = _serah.bentuk(s)
            nomor = sj["fulfillment_number"]
            await catat_riwayat(
                conn, tid, "invoice_fulfillments", fid, nomor, "SJ_SERAH_CHANGED", ctx["user_id"],
                f"Serah-terima Surat Jalan {nomor} diubah",
                {"lama": {k: lama[k] for k in ("metode", "kirim_ke", "kirim_ke_sumber", "diterima_pada", "resi")},
                 "baru": baru}, source="api:deliveries.serah")
    return {"success": True, "data": {**baru, "lampiran_jumlah": lama["lampiran_jumlah"]}}


@router.get("/{delivery_id}/pdf")
async def get_delivery_pdf(
    delivery_id: str,
    request: Request,
):
    """
    Generate Surat Jalan PDF for a delivery.
    Returns PDF bytes inline (for browser preview / download).
    """
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        _m = await muat_pdf_surat_jalan(conn, ctx, delivery_id)
    delivery_data, row = _m["delivery_data"], _m["row"]


    pdf_service = get_pdf_service()
    pdf_bytes = pdf_service.generate_delivery_note_pdf(delivery_data)

    delivery_num = row["delivery_number"] or delivery_id[:8]
    from ..utils.content_disposition import pdf_content_disposition, sanitize_filename
    filename = sanitize_filename(delivery_num) + ".pdf"

    return StreamingResponse(
        BytesIO(pdf_bytes),
        media_type="application/pdf",
        headers={
            "Content-Disposition": pdf_content_disposition(delivery_num),
            "Cache-Control": "private, max-age=300",
        },
    )
