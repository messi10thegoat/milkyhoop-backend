"""
Proformas (Faktur Proforma / Tagihan Uang Muka) Router — T200.

Proforma = dokumen PENAGIH UANG MUKA yang merujuk sebuah Sales Order.

MUTLAK NON-POSTING. Berkas ini TIDAK PERNAH menyentuh journal_entries /
journal_lines. Uang tetap masuk lewat customer_deposits (yang menjurnal).

Dua aturan yang menopang kebenarannya:
  1. WHERE tenant_id = $1 di SETIAP query. Gateway konek dengan peran BYPASSRLS,
     jadi RLS TIDAK melindungi jalur ini — klausa inilah penjaga sebenarnya.
  2. TERBAYAR = TURUNAN. Tidak ada kolom terbayar yang disimpan. Angka dihitung
     dari customer_deposits.proforma_id. Atribusi TIDAK PERNAH memakai tanggal
     (tanggal pecah pada cicilan / dua proforma di hari yang sama).
"""

import base64
import logging
import uuid as uuid_module
from datetime import date, datetime
from decimal import Decimal
from io import BytesIO
from pathlib import Path as _Path
from typing import Literal, Optional

import asyncpg
from fastapi import APIRouter, HTTPException, Query, Request

from ..utils.tanggal_tenant import tanggal_dokumen
from ..services import faktur_cetak as _fc_snap
from ..services.proforma_terbayar import terbayar_proforma
from ..services.proforma_atribusi import muat_atribusi
from ..services.so_riwayat import catat_riwayat
from ..utils.idempotency import ambil_replay_klien, hash_payload, kunci_idempotensi_klien, simpan_replay_klien
from fastapi.responses import StreamingResponse
from fastapi import Response as _Response
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


def _rp2(x) -> str:
    """Rupiah Indonesia: titik ribuan, koma desimal (mis. 1.000.000,00)."""
    return f"{float(x):,.2f}".translate(str.maketrans({",": ".", ".": ","}))


router = APIRouter()
so_router = APIRouter()  # dipasang di /api/sales-orders

# Status SO yang boleh ditagih lewat proforma: 'confirmed' ke atas.
SO_BILLABLE_STATUSES = (
    "confirmed",
    "partial_shipped",
    "shipped",
    "partial_invoiced",
    "invoiced",
    "completed",
)

VALID_PURPOSES = ("DP", "TERMIN", "PELUNASAN")


def _normalisasi_purpose(nilai):
    """Rapikan `purpose` sebelum dicocokkan ke VALID_PURPOSES.

    Enumnya HURUF BESAR, dan sebelum ini dicocokkan mentah-mentah: `"dp"`,
    `"Dp"`, atau `" DP "` sama-sama ditolak 400 padahal maksud penggunanya
    tidak ambigu sedikit pun. Yang ditolak seharusnya nilai yang MEMANG di luar
    daftar (mis. "lunas"), bukan selisih kapitalisasi atau spasi tempel dari
    form.

    Ini PENGERASAN, BUKAN PELEBARAN: himpunan nilai yang diterima tetap tiga.
    Nilai yang dikembalikan inilah yang disimpan, sehingga kolomnya tetap
    kanonik huruf besar dan pembaca hilir tak perlu ikut menormalisasi.

    None diteruskan apa adanya — pada PATCH ia berarti "jangan ubah".
    """
    if nilai is None:
        return None
    return str(nilai).strip().upper()


async def get_pool() -> asyncpg.Pool:
    """Get singleton connection pool (Law 32)."""
    from ..services.db_pool import get_db_pool

    return await get_db_pool()


def get_user_context(request: Request) -> dict:
    """Extract user context from request."""
    if not hasattr(request.state, "user") or not request.state.user:
        raise HTTPException(status_code=401, detail="Authentication required")

    user = request.state.user
    tenant_id = user.get("tenant_id")
    user_id = user.get("user_id") or user.get("id")

    if not tenant_id:
        raise HTTPException(status_code=401, detail="Invalid user context")

    return {
        "tenant_id": tenant_id,
        "user_id": uuid_module.UUID(user_id) if user_id else None,
    }


def _uuid_or_404(value: str, what: str = "Proforma") -> uuid_module.UUID:
    try:
        return uuid_module.UUID(str(value))
    except (ValueError, AttributeError, TypeError):
        raise HTTPException(status_code=404, detail=f"{what} not found")


def _f(value) -> Optional[float]:
    """Decimal -> float. Aturan repo: RESPONS pakai float, JANGAN Decimal
    (pydantic v2 menyerialkan Decimal sebagai STRING -> merusak matematika FE)."""
    if value is None:
        return None
    return float(value)


# ============================================================================
# SCHEMAS
# ============================================================================


class CreateProformaRequest(BaseModel):
    sales_order_id: str
    purpose: str = "DP"
    percent_of_order: Optional[Decimal] = None
    amount: Optional[Decimal] = None
    proforma_date: Optional[date] = None
    due_date: Optional[date] = None
    terms: Optional[str] = None
    notes: Optional[str] = None
    currency: Optional[str] = "IDR"
    payment_bank_name: Optional[str] = None
    payment_account_number: Optional[str] = None
    payment_account_holder: Optional[str] = None


class UpdateProformaRequest(BaseModel):
    purpose: Optional[str] = None
    percent_of_order: Optional[Decimal] = None
    amount: Optional[Decimal] = None
    proforma_date: Optional[date] = None
    due_date: Optional[date] = None
    terms: Optional[str] = None
    notes: Optional[str] = None
    payment_bank_name: Optional[str] = None
    payment_account_number: Optional[str] = None
    payment_account_holder: Optional[str] = None


class CancelProformaRequest(BaseModel):
    reason: str = Field(..., min_length=1)


# ============================================================================
# HELPERS (pagar)
# ============================================================================


async def compute_paid_amount(conn, tenant_id: str, proforma_id) -> float:
    """TERBAYAR = TURUNAN. Dihitung dari customer_deposits yang MENUNJUK proforma
    ini lewat proforma_id. BUKAN dari kolom tersimpan, BUKAN dari tanggal."""
    row = await conn.fetchrow(
        """
        SELECT COALESCE(SUM(amount), 0) AS paid, COUNT(*) AS n
        FROM customer_deposits
        WHERE proforma_id = $1 AND tenant_id = $2 AND status <> 'void'
        """,
        proforma_id,
        tenant_id,
    )
    return float(row["paid"] or 0)


async def terbayar_satu(conn, tenant_id: str, row) -> tuple:
    """(paid float, paid_breakdown) untuk TAMPILAN — turunan dari SO (services/proforma_terbayar).
    Pagar batal TIDAK memakai ini (tetap compute_paid_amount = uang muka eksplisit)."""
    tb = await terbayar_proforma(conn, tenant_id, [row["sales_order_id"]])
    t = tb.get(row["id"])
    return (float(t["paid"]), t["paid_breakdown"]) if t else (0.0, None)


async def issued_total_for_order(
    conn, tenant_id: str, sales_order_id, exclude_id=None
) -> float:
    """Jumlah amount proforma berstatus 'issued' SAJA untuk satu SO.
    'draft', 'cancelled', dan 'expired' DIKECUALIKAN — kalau 'expired' ikut
    dihitung, SO terkunci dari penagihan ulang."""
    row = await conn.fetchrow(
        """
        SELECT COALESCE(SUM(amount), 0) AS total
        FROM proformas
        WHERE tenant_id = $1
          AND sales_order_id = $2
          AND status = 'issued'
          AND ($3::uuid IS NULL OR id <> $3::uuid)
        """,
        tenant_id,
        sales_order_id,
        exclude_id,
    )
    return float(row["total"] or 0)


async def deposit_totals_for_order(conn, tenant_id: str, sales_order_id) -> dict:
    """DITERIMA = TURUNAN. Dua angka, satu query, atribusi lewat sales_order_id
    dan proforma_id — TIDAK PERNAH lewat tanggal.

    received_total   = semua customer_deposits milik SO ini yang bukan 'void'.
    unbilled_received = bagian dari itu yang TIDAK menunjuk proforma mana pun.
      Angka ini sengaja tidak disembunyikan: uang yang masuk tanpa tagihan
      adalah uang yang belum diatribusikan, dan user perlu melihatnya.

    `void` dikecualikan karena depositnya sudah dibatalkan; 'applied' TETAP
    dihitung karena uangnya benar-benar diterima, hanya sudah dipakai.
    """
    row = await conn.fetchrow(
        """
        SELECT COALESCE(SUM(amount), 0) AS received,
               COALESCE(SUM(amount) FILTER (WHERE proforma_id IS NULL), 0) AS unbilled
        FROM customer_deposits
        WHERE tenant_id = $1
          AND sales_order_id = $2
          AND status <> 'void'
        """,
        tenant_id,
        sales_order_id,
    )
    return {
        "received_total": float(row["received"] or 0),
        "unbilled_received": float(row["unbilled"] or 0),
    }


def sisa_bisa_ditagih(order_total: float, issued_total: float, tak_tertagih: float) -> float:
    """SATU rumus sisa yang boleh ditagih (MASTER 28 Sep 2026, celah 2):
        max(0, order_total - issued_total - uang_muka_di_luar_tagihan)
    uang_muka_di_luar_tagihan = services.proforma_atribusi (tautan menang; tanpa tautan -> pencocokan nominal ke proforma
    issued yang terbuka; sisanya di luar tagihan). Uang muka yang MEMBAYAR proforma tak dikurangkan dua kali; uang muka
    TANPA tagihan mengurangi plafon (dulu tidak -> PELUNASAN bisa menagih penuh = tagihan ganda)."""
    return round(max(0.0, float(order_total) - float(issued_total) - float(tak_tertagih)), 2)


async def rincian_tagih(conn, tenant_id: str, sales_order_id, order_total: float, exclude_id=None) -> dict:
    """Rincian plafon tagihan SO -- dipakai pagar (buat/ubah/terbit) DAN GET /sales-orders/{id}/proformas."""
    issued = await issued_total_for_order(conn, tenant_id, sales_order_id, exclude_id=exclude_id)
    a = (await muat_atribusi(conn, tenant_id, [sales_order_id], exclude_proforma_id=exclude_id))[sales_order_id]
    return {
        "order_total": round(float(order_total), 2),
        "issued_total": round(issued, 2),
        "received_total": round(float(a["diterima"]), 2),
        "received_not_billed": round(float(a["tak_tertagih"]), 2),
        "billable_remaining": sisa_bisa_ditagih(order_total, issued, a["tak_tertagih"]),
    }


async def assert_within_order_total(
    conn, tenant_id: str, sales_order_id, order_total: float, amount: float, exclude_id=None
):
    """Pagar total: amount ini tidak boleh melebihi sisa_bisa_ditagih (issued lain + uang muka diterima).
    Ditolak dengan pesan yang MENYEBUT komponennya."""
    r = await rincian_tagih(conn, tenant_id, sales_order_id, order_total, exclude_id=exclude_id)
    sisa = r["billable_remaining"]
    if round(amount, 2) > sisa + 0.005:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Nilai proforma {_rp2(amount)} melebihi sisa yang bisa ditagih. "
                f"Nilai Sales Order {_rp2(order_total)}, sudah ditagih (issued) {_rp2(r['issued_total'])}, "
                f"uang muka diterima {_rp2(r['received_total'])} "
                f"(di luar tagihan {_rp2(r['received_not_billed'])}), "
                f"sisa yang bisa ditagih {_rp2(sisa)}."
            ),
        )


async def fetch_order_or_404(conn, tenant_id: str, sales_order_id):
    order = await conn.fetchrow(
        """
        SELECT id, order_number, customer_id, customer_name, total_amount, status,
               payment_bank_name, payment_account_number, payment_account_holder
        FROM sales_orders
        WHERE id = $1 AND tenant_id = $2
        """,
        sales_order_id,
        tenant_id,
    )
    if not order:
        raise HTTPException(status_code=404, detail="Sales Order not found")
    return order


def serialize_proforma(row, order_number=None, paid_amount=None, paid_breakdown=None) -> dict:
    amount = _f(row["amount"]) or 0.0
    data = {
        "id": str(row["id"]),
        "proforma_number": row["proforma_number"],
        "proforma_date": row["proforma_date"].isoformat() if row["proforma_date"] else None,
        "due_date": row["due_date"].isoformat() if row["due_date"] else None,
        "sales_order_id": str(row["sales_order_id"]),
        "sales_order_number": order_number,
        "customer_id": str(row["customer_id"]) if row["customer_id"] else None,
        "customer_name": row["customer_name"],
        "purpose": row["purpose"],
        "percent_of_order": _f(row["percent_of_order"]),
        "amount": amount,
        "currency": row["currency"],
        "terms": row["terms"],
        "notes": row["notes"],
        "payment_bank_name": row["payment_bank_name"],
        "payment_account_number": row["payment_account_number"],
        "payment_account_holder": row["payment_account_holder"],
        "status": row["status"],
        "issued_at": row["issued_at"].isoformat() if row["issued_at"] else None,
        "cancelled_at": row["cancelled_at"].isoformat() if row["cancelled_at"] else None,
        "cancelled_reason": row["cancelled_reason"],
        "created_at": row["created_at"].isoformat() if row["created_at"] else None,
        "updated_at": row["updated_at"].isoformat() if row["updated_at"] else None,
    }
    if paid_amount is not None:
        data["paid_amount"] = float(paid_amount)
        data["outstanding_amount"] = round(amount - float(paid_amount), 2)
        data["is_fully_paid"] = float(paid_amount) + 0.005 >= amount
    if paid_breakdown is not None:
        data["paid_breakdown"] = paid_breakdown
    return data


# ============================================================================
# READ
# ============================================================================


@router.get("")
async def list_proformas(
    request: Request,
    # Kosakata = CHECK constraint tabel (hidup di MIGRASI, jadi ia KODE) UNION
    # nilai khusus yang punya CABANG SENDIRI di handler ini. Mengambilnya dari
    # `SELECT DISTINCT status` akan MEMBEKUKAN DRIFT jadi spesifikasi: `posted`
    # ada di constraint sales_invoices tapi nol baris memakainya, dan
    # `unpaid`/`active`/`overdue`/`all` tak pernah tersimpan sebagai nilai kolom
    # sama sekali -- mereka dihitung. Sebelum ini `status` adalah str polos,
    # jadi nilai asing dijawab 200 daftar kosong dan pemanggil tak bisa
    # membedakan "tidak ada data" dari "parameter tidak dimengerti".
    status: Optional[
        Literal["all", "draft", "issued", "cancelled", "expired"]
    ] = Query("all"),
    customer_id: Optional[str] = Query(None),
    sales_order_id: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
):
    """List proformas with filters."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            # tenant_id SELALU kondisi pertama dan tertulis LITERAL di tiap SQL.
            extras = []
            params = [ctx["tenant_id"]]
            idx = 2

            if status and status != "all":
                extras.append(f"p.status = ${idx}")
                params.append(status)
                idx += 1
            if customer_id:
                extras.append(f"p.customer_id = ${idx}::uuid")
                params.append(_uuid_or_404(customer_id, "Customer"))
                idx += 1
            if sales_order_id:
                extras.append(f"p.sales_order_id = ${idx}::uuid")
                params.append(_uuid_or_404(sales_order_id, "Sales Order"))
                idx += 1
            if search:
                from ..services.kode_order import sql_cari_so_induk
                extras.append(
                    f"(p.proforma_number ILIKE ${idx} OR p.customer_name ILIKE ${idx} "
                    f"OR p.search_text ILIKE ${idx} OR p.customer_id::text IN "
                    f"(SELECT c.id::text FROM customers c WHERE c.tenant_id = p.tenant_id "
                    f"AND c.search_text ILIKE ${idx}) OR "
                    + sql_cari_so_induk("proforma", "p", "$1", f"${idx}") + ")"
                )
                params.append(f"%{search}%")
                idx += 1

            extra = ("".join(f" AND {c}" for c in extras)) if extras else ""

            total = await conn.fetchval(
                f"SELECT COUNT(*) FROM proformas p WHERE p.tenant_id = $1{extra}",
                *params,
            )

            rows = await conn.fetch(
                f"""
                SELECT p.*, so.order_number
                FROM proformas p
                LEFT JOIN sales_orders so
                       ON so.id = p.sales_order_id AND so.tenant_id = p.tenant_id
                WHERE p.tenant_id = $1{extra}
                ORDER BY p.proforma_date DESC, p.created_at DESC
                LIMIT ${idx} OFFSET ${idx + 1}
                """,
                *params,
                limit,
                skip,
            )

            tb = await terbayar_proforma(conn, ctx["tenant_id"], [r["sales_order_id"] for r in rows])
            items = [
                serialize_proforma(r, r["order_number"], float(tb[r["id"]]["paid"]) if r["id"] in tb else 0.0,
                                   tb[r["id"]]["paid_breakdown"] if r["id"] in tb else None)
                for r in rows
            ]
            from ..services.kode_order import tempel_kode as _tempel_kode
            await _tempel_kode(conn, ctx["tenant_id"], "proforma", items)

            page = (skip // limit) + 1 if limit > 0 else 1
            total_pages = (total + limit - 1) // limit if limit > 0 else 1

            return {
                "items": items,
                "total": total,
                "has_more": (skip + limit) < total,
                "page": page,
                "limit": limit,
                "total_pages": total_pages,
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error listing proformas: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to list proformas")


@router.get("/summary")
async def get_proforma_summary(request: Request):
    """Kartu ringkasan Proforma (U2 CW, 4 Okt 2026), pola /sales-orders/summary. Terbayar = SUMBER YANG SAMA dengan
    daftar (services/proforma_terbayar.terbayar_proforma, atribusi bersama plafon & PDF) dan RUMUS yang sama dengan
    serialize_proforma (lunas = terbayar + 0,005 >= nominal; sisa = nominal - terbayar). Tenant eksplisit. DI ATAS
    /{proforma_id}."""
    ctx = get_user_context(request)
    tid = ctx["tenant_id"]
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT id, sales_order_id, status, amount FROM proformas WHERE tenant_id = $1", tid)
        terbit = [r for r in rows if r["status"] == "issued"]
        tb = await terbayar_proforma(conn, tid, [r["sales_order_id"] for r in terbit])
    per = {}
    for r in rows:
        n, j = per.get(r["status"], (0, Decimal("0")))
        per[r["status"]] = (n + 1, j + Decimal(str(r["amount"] or 0)))
    terbayar = sisa = Decimal("0")
    lunas = sebagian = belum = 0
    for r in terbit:
        nominal = Decimal(str(r["amount"] or 0))
        bayar = Decimal(str(tb[r["id"]]["paid"])) if r["id"] in tb else Decimal("0")
        terbayar += bayar
        sisa += max(Decimal("0"), nominal - bayar)
        if bayar + Decimal("0.005") >= nominal:
            lunas += 1
        elif bayar > 0:
            sebagian += 1
        else:
            belum += 1
    def _n(s): return per.get(s, (0, Decimal("0")))[0]
    def _j(s): return float(per.get(s, (0, Decimal("0")))[1])
    return {"success": True, "data": {
        "total_count": len(rows),
        "draft_count": _n("draft"), "draft_amount": _j("draft"),
        "issued_count": _n("issued"), "issued_amount": _j("issued"),
        "issued_paid_amount": float(terbayar), "issued_outstanding_amount": float(sisa),
        "issued_paid_count": lunas, "issued_partially_paid_count": sebagian, "issued_unpaid_count": belum,
        "cancelled_count": _n("cancelled"), "cancelled_amount": _j("cancelled"),
        "expired_count": _n("expired"),
    }}


@router.get("/{proforma_id}/history")
async def get_proforma_history(request: Request, proforma_id: str, limit: int = Query(200, ge=1, le=500)):
    """Riwayat proforma, bentuk SAMA dengan GET /sales-orders/{id}/history (services/so_riwayat.riwayat_proforma)."""
    ctx = get_user_context(request)
    pid = _uuid_or_404(proforma_id)
    from ..services.dashboard_izin import boleh_baca
    from ..services.so_riwayat import riwayat_proforma
    pool = await get_pool()
    async with pool.acquire() as conn:
        data = await riwayat_proforma(conn, ctx["tenant_id"], pid, lambda m: boleh_baca(request, m), limit)
    if data is None:
        raise HTTPException(status_code=404, detail="Proforma not found")
    return {"success": True, "data": data}


@router.get("/{proforma_id}")
async def get_proforma_detail(request: Request, proforma_id: str):
    """Get one proforma. `paid_amount` adalah TURUNAN dari customer_deposits."""
    try:
        ctx = get_user_context(request)
        pid = _uuid_or_404(proforma_id)
        pool = await get_pool()

        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT p.*, so.order_number, so.total_amount AS order_total_amount
                FROM proformas p
                LEFT JOIN sales_orders so
                       ON so.id = p.sales_order_id AND so.tenant_id = p.tenant_id
                WHERE p.id = $1 AND p.tenant_id = $2
                """,
                pid,
                ctx["tenant_id"],
            )
            if not row:
                raise HTTPException(status_code=404, detail="Proforma not found")

            paid, paid_breakdown = await terbayar_satu(conn, ctx["tenant_id"], row)
            deposits = await conn.fetch(
                """
                SELECT id, deposit_number, deposit_date, amount, status
                FROM customer_deposits
                WHERE proforma_id = $1 AND tenant_id = $2 AND status <> 'void'
                ORDER BY deposit_date, created_at
                """,
                pid,
                ctx["tenant_id"],
            )

            data = serialize_proforma(row, row["order_number"], paid, paid_breakdown)
            data["order_total_amount"] = _f(row["order_total_amount"])
            data["deposits"] = [
                {
                    "id": str(d["id"]),
                    "deposit_number": d["deposit_number"],
                    "deposit_date": d["deposit_date"].isoformat()
                    if d["deposit_date"]
                    else None,
                    "amount": _f(d["amount"]),
                    "status": d["status"],
                }
                for d in deposits
            ]

            from ..services.kode_order import tempel_kode as _tempel_kode
            await _tempel_kode(conn, ctx["tenant_id"], "proforma", [data])
            return {"success": True, "data": data}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting proforma {proforma_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get proforma")


@so_router.get("/{order_id}/proformas")
async def list_proformas_for_order(request: Request, order_id: str):
    """Semua proforma milik satu Sales Order."""
    try:
        ctx = get_user_context(request)
        oid = _uuid_or_404(order_id, "Sales Order")
        pool = await get_pool()

        async with pool.acquire() as conn:
            order = await fetch_order_or_404(conn, ctx["tenant_id"], oid)

            rows = await conn.fetch(
                """
                SELECT p.*
                FROM proformas p
                WHERE p.tenant_id = $1 AND p.sales_order_id = $2
                ORDER BY p.proforma_date, p.created_at
                """,
                ctx["tenant_id"],
                oid,
            )

            tb = await terbayar_proforma(conn, ctx["tenant_id"], [oid])
            items = [
                serialize_proforma(
                    r, order["order_number"], float(tb[r["id"]]["paid"]) if r["id"] in tb else 0.0,
                    tb[r["id"]]["paid_breakdown"] if r["id"] in tb else None,
                )
                for r in rows
            ]
            order_total = _f(order["total_amount"]) or 0.0
            rincian = await rincian_tagih(conn, ctx["tenant_id"], oid, order_total)
            issued_total = rincian["issued_total"]
            # T201: agregat "diterima". Dihitung, tidak disimpan — tak satu pun
            # kolom ringkasan ditambahkan ke tabel mana pun.
            diterima = await deposit_totals_for_order(conn, ctx["tenant_id"], oid)

            return {
                "items": items,
                "total": len(items),
                "has_more": False,
                # `order_total_amount` dipertahankan (dipakai FE sejak T200);
                # `order_total` adalah nama kanonik panel agregat.
                "order_total_amount": order_total,
                "order_total": order_total,
                "issued_total": issued_total,
                "billable_remaining": rincian["billable_remaining"],
                "billable_breakdown": {k: rincian[k] for k in (
                    "order_total", "issued_total", "received_total", "received_not_billed")},
                "received_total": diterima["received_total"],
                "unbilled_received": diterima["unbilled_received"],
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error listing proformas for order {order_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to list proformas")


# ============================================================================
# WRITE (NON-POSTING — nol sentuhan journal_entries)
# ============================================================================


@router.post("", status_code=201)
async def create_proforma(request: Request, body: CreateProformaRequest):
    """Buat proforma (status draft) dari sebuah Sales Order."""
    try:
        ctx = get_user_context(request)
        oid = _uuid_or_404(body.sales_order_id, "Sales Order")

        _purpose = _normalisasi_purpose(body.purpose)
        if _purpose not in VALID_PURPOSES:
            raise HTTPException(
                status_code=400,
                detail=f"purpose harus salah satu dari {list(VALID_PURPOSES)}",
            )

        try:
            kunci_klien = kunci_idempotensi_klien(request)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

        pool = await get_pool()
        async with pool.acquire() as conn:
            async with conn.transaction():
                # C3 (26 Sep 2026): dulu tanpa transaksi/kunci/kunci-idempotensi -> klik ganda = DUA draf DP,
                # dan keduanya bisa diterbitkan (plafon hanya menghitung 'issued'). Kini: (1) kunci SO yang SAMA
                # dengan terbit (serialkan plafon per SO); (2) X-Idempotency-Key klien -> replay / 409 bila isi beda.
                await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", f"PROFORMA_SO:{ctx['tenant_id']}:{oid}")
                kunci_penuh = sidik = None
                if kunci_klien:
                    kunci_penuh = f"PROFORMA_CREATE:{ctx['user_id']}:{kunci_klien}"
                    sidik = hash_payload(body.model_dump(mode="json"))
                    await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", f"IDEM:{ctx['tenant_id']}:{kunci_penuh}")
                    try:
                        lama = await ambil_replay_klien(conn, ctx["tenant_id"], kunci_penuh, sidik)
                    except LookupError as e:
                        asli = ((getattr(e, "respons", None) or {}).get("data") or {})
                        raise HTTPException(status_code=409, detail={
                            "code": "IDEMPOTENCY_KEY_REUSED",
                            "message": "Idempotency-Key sudah dipakai untuk proforma lain dengan isi berbeda",
                            "proforma_id": asli.get("id"), "proforma_number": asli.get("proforma_number"),
                        })
                    if lama is not None:
                        return lama
                order = await fetch_order_or_404(conn, ctx["tenant_id"], oid)

                if order["status"] not in SO_BILLABLE_STATUSES:
                    raise HTTPException(  # 3 Okt: kode stabil (bentuk sama dgn SO_NOT_ACCEPTING_DEPOSIT)
                        status_code=400,
                        detail={"code": "SO_NOT_BILLABLE", "message": (
                            f"Sales Order berstatus '{order['status']}' tidak bisa ditagih "
                            f"dengan proforma. Harus 'confirmed' ke atas."
                        )},
                    )

                order_total = _f(order["total_amount"]) or 0.0

                percent = _f(body.percent_of_order)
                amount = _f(body.amount)
                if percent is None and amount is None:
                    raise HTTPException(
                        status_code=400,
                        detail="Wajib mengisi salah satu: percent_of_order atau amount.",
                    )
                if percent is not None:
                    if percent <= 0 or percent > 100:
                        raise HTTPException(
                            status_code=400, detail="percent_of_order harus di antara 0 dan 100."
                        )
                    amount = round(order_total * percent / 100.0, 2)
                if amount is None or amount <= 0:
                    raise HTTPException(status_code=400, detail="amount harus lebih besar dari 0.")

                await assert_within_order_total(
                    conn, ctx["tenant_id"], oid, order_total, amount
                )

                number = await conn.fetchval(
                    "SELECT generate_proforma_number($1)", ctx["tenant_id"]
                )

                row = await conn.fetchrow(
                    """
                    INSERT INTO proformas (
                        tenant_id, proforma_number, proforma_date, due_date,
                        sales_order_id, customer_id, customer_name,
                        purpose, percent_of_order, amount, currency, terms, notes,
                        payment_bank_name, payment_account_number, payment_account_holder,
                        status, created_by
                    ) VALUES (
                        $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13,
                        $14, $15, $16, 'draft', $17
                    )
                    RETURNING *
                    """,
                    ctx["tenant_id"],
                    number,
                    body.proforma_date or await tanggal_dokumen(conn, ctx["tenant_id"]),  # t10-tanggal-bisnis
                    body.due_date,
                    oid,
                    order["customer_id"],
                    order["customer_name"],
                    _purpose,
                    Decimal(str(percent)) if percent is not None else None,
                    Decimal(str(amount)),
                    body.currency or "IDR",
                    body.terms,
                    body.notes,
                    body.payment_bank_name or order["payment_bank_name"],
                    body.payment_account_number or order["payment_account_number"],
                    await _fc_snap.pemilik_cetak(conn, ctx["tenant_id"], body.payment_bank_name or order["payment_bank_name"], body.payment_account_number or order["payment_account_number"], body.payment_account_holder or order["payment_account_holder"]),  # 28 Sep: snapshot pemilik rekening, bukan nama akun
                    ctx["user_id"],
                )

                hasil = {
                    "success": True,
                    "data": serialize_proforma(row, order["order_number"], 0.0),
                }
                if kunci_penuh:
                    await simpan_replay_klien(conn, ctx["tenant_id"], kunci_penuh, "PROFORMA_CREATE", sidik,
                                              hasil, row["id"])
                return hasil

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating proforma: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to create proforma")


@router.patch("/{proforma_id}")
async def update_proforma(request: Request, proforma_id: str, body: UpdateProformaRequest):
    """Ubah proforma. HANYA saat status 'draft'."""
    try:
        ctx = get_user_context(request)
        pid = _uuid_or_404(proforma_id)
        pool = await get_pool()

        async with pool.acquire() as conn:
            cur = await conn.fetchrow(
                "SELECT * FROM proformas WHERE id = $1 AND tenant_id = $2",
                pid,
                ctx["tenant_id"],
            )
            if not cur:
                raise HTTPException(status_code=404, detail="Proforma not found")
            if cur["status"] != "draft":
                raise HTTPException(
                    status_code=400,
                    detail=f"Proforma berstatus '{cur['status']}' tidak bisa diubah. Hanya 'draft'.",
                )

            order = await fetch_order_or_404(conn, ctx["tenant_id"], cur["sales_order_id"])
            order_total = _f(order["total_amount"]) or 0.0

            _purpose = _normalisasi_purpose(body.purpose)
            if _purpose is not None and _purpose not in VALID_PURPOSES:
                raise HTTPException(
                    status_code=400,
                    detail=f"purpose harus salah satu dari {list(VALID_PURPOSES)}",
                )

            percent = _f(body.percent_of_order)
            amount = _f(body.amount)
            if percent is not None:
                if percent <= 0 or percent > 100:
                    raise HTTPException(
                        status_code=400, detail="percent_of_order harus di antara 0 dan 100."
                    )
                amount = round(order_total * percent / 100.0, 2)
            if amount is not None:
                if amount <= 0:
                    raise HTTPException(
                        status_code=400, detail="amount harus lebih besar dari 0."
                    )
                await assert_within_order_total(
                    conn, ctx["tenant_id"], cur["sales_order_id"], order_total, amount,
                    exclude_id=pid,
                )

            row = await conn.fetchrow(
                """
                UPDATE proformas SET
                    purpose = COALESCE($3, purpose),
                    percent_of_order = CASE WHEN $4::numeric IS NOT NULL THEN $4::numeric
                                            WHEN $5::numeric IS NOT NULL THEN NULL
                                            ELSE percent_of_order END,
                    amount = COALESCE($6::numeric, amount),
                    proforma_date = COALESCE($7::date, proforma_date),
                    due_date = COALESCE($8::date, due_date),
                    terms = COALESCE($9, terms),
                    notes = COALESCE($10, notes),
                    payment_bank_name = COALESCE($11, payment_bank_name),
                    payment_account_number = COALESCE($12, payment_account_number),
                    payment_account_holder = COALESCE($13, payment_account_holder)
                WHERE id = $1 AND tenant_id = $2
                RETURNING *
                """,
                pid,
                ctx["tenant_id"],
                _purpose,
                Decimal(str(percent)) if percent is not None else None,
                Decimal(str(body.amount)) if body.amount is not None else None,
                Decimal(str(amount)) if amount is not None else None,
                body.proforma_date,
                body.due_date,
                body.terms,
                body.notes,
                body.payment_bank_name,
                body.payment_account_number,
                body.payment_account_holder,
            )

            paid, paid_breakdown = await terbayar_satu(conn, ctx["tenant_id"], row)
            return {
                "success": True,
                "data": serialize_proforma(row, order["order_number"], paid, paid_breakdown),
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating proforma {proforma_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to update proforma")


# ============================================================================
# TERBIT / BATAL -- SATU penentu untuk TULIS dan PRATINJAU (U2 Proforma CW, 4 Okt 2026, MASTER GO).
# Penentu MENGUMPULKAN semua blok; jalur tulis mengangkat blok PERTAMA dengan status + detail yang SAMA seperti
# sebelumnya (urutan sama: status -> SO -> plafon; batal: status -> alasan -> terbayar). NON-POSTING: nol jurnal.
# ============================================================================

def _blok(code: str, status: int, detail) -> dict:
    pesan = detail if isinstance(detail, str) else (detail.get("message") if isinstance(detail, dict) else str(detail))
    return {"code": code, "status": status, "detail": detail, "message": pesan}


def _angkat_blok_pertama(r: dict) -> None:
    if r["blocks"]:
        b = r["blocks"][0]
        raise HTTPException(status_code=b["status"], detail=b["detail"])


async def _kunci_proforma(conn, ctx: dict, pid):
    """Proforma tenant ini + kunci SO yang SAMA dengan buat/terbit (C3 PROFORMA_SO), lalu baca ULANG di bawah kunci."""
    cur = await conn.fetchrow("SELECT * FROM proformas WHERE id = $1 AND tenant_id = $2", pid, ctx["tenant_id"])
    if not cur:
        raise HTTPException(status_code=404, detail="Proforma not found")
    await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))",
                       f"PROFORMA_SO:{ctx['tenant_id']}:{cur['sales_order_id']}")
    return await conn.fetchrow("SELECT * FROM proformas WHERE id = $1 AND tenant_id = $2", pid, ctx["tenant_id"])


def _ringkas_tagih(r: dict) -> dict:
    return {k: r[k] for k in ("order_total", "issued_total", "received_total", "received_not_billed",
                              "billable_remaining")}


async def _rencana_terbit(conn, ctx: dict, cur) -> dict:
    tid, pid = ctx["tenant_id"], cur["id"]
    blocks = []
    if cur["status"] != "draft":
        blocks.append(_blok("PROFORMA_NOT_DRAFT", 400,
                            f"Hanya proforma 'draft' yang bisa diterbitkan (sekarang '{cur['status']}')."))
    order = await fetch_order_or_404(conn, tid, cur["sales_order_id"])
    if order["status"] not in SO_BILLABLE_STATUSES:
        blocks.append(_blok("SO_NOT_BILLABLE", 400, {
            "code": "SO_NOT_BILLABLE", "message": f"Sales Order berstatus '{order['status']}' tidak bisa ditagih."}))
    total, nominal = _f(order["total_amount"]) or 0.0, _f(cur["amount"]) or 0.0
    try:  # pagar plafon YANG SAMA dengan buat/ubah (assert_within_order_total)
        await assert_within_order_total(conn, tid, cur["sales_order_id"], total, nominal, exclude_id=pid)
    except HTTPException as e:
        blocks.append(_blok("PROFORMA_EXCEEDS_BILLABLE", e.status_code, e.detail))
    sebelum = await rincian_tagih(conn, tid, cur["sales_order_id"], total, exclude_id=pid)
    return {"order": order, "blocks": blocks, "linked_deposits": [],
            "impact": {"amount": nominal, "before": _ringkas_tagih(sebelum)}}


async def _rencana_batal(conn, ctx: dict, cur, alasan) -> dict:
    tid, pid = ctx["tenant_id"], cur["id"]
    blocks = []
    if cur["status"] == "cancelled":
        blocks.append(_blok("PROFORMA_ALREADY_CANCELLED", 400, "Proforma sudah dibatalkan."))
    if not (alasan or "").strip():
        blocks.append(_blok("CANCEL_REASON_REQUIRED", 422, "Alasan pembatalan wajib diisi."))
    dps = await conn.fetch(
        """SELECT id, deposit_number, amount, status FROM customer_deposits
           WHERE proforma_id = $1 AND tenant_id = $2 AND status <> 'void' ORDER BY created_at""", pid, tid)
    terbayar = await compute_paid_amount(conn, tid, pid)  # pagar = uang muka EKSPLISIT (bukan atribusi tampilan)
    if terbayar > 0:
        blocks.append(_blok("PROFORMA_HAS_PAYMENT", 400, (
            f"Proforma sudah menerima pembayaran {terbayar:,.2f}. "
            f"Tidak bisa dibatalkan — lakukan refund uang muka terlebih dahulu.")))
    order = await fetch_order_or_404(conn, tid, cur["sales_order_id"])
    total = _f(order["total_amount"]) or 0.0
    sebelum = await rincian_tagih(conn, tid, cur["sales_order_id"], total)
    return {"order": order, "blocks": blocks,
            "linked_deposits": [{"id": str(d["id"]), "deposit_number": d["deposit_number"],
                                 "amount": float(d["amount"]), "status": d["status"]} for d in dps],
            "impact": {"amount": _f(cur["amount"]) or 0.0, "before": _ringkas_tagih(sebelum)}}


def _bentuk_pratinjau(cur, r: dict) -> dict:
    return {"proforma_id": str(cur["id"]), "proforma_number": cur["proforma_number"], "status_now": cur["status"],
            "can_proceed": not r["blocks"],
            "blocks": [{"code": b["code"], "message": b["message"]} for b in r["blocks"]],
            "sales_order": {"id": str(r["order"]["id"]), "order_number": r["order"]["order_number"]},
            "impact": r["impact"], "linked_deposits": r["linked_deposits"], "preview": True}


async def _idem_aksi(conn, ctx: dict, request, aksi: str, pid, isi: dict, response):
    """X-Idempotency-Key opsional (pola W0): (kunci_penuh, sidik, respons_lama|None). Kunci sama + isi beda -> 409."""
    try:
        kunci = kunci_idempotensi_klien(request)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not kunci:
        return None, None, None
    kp = f"PROFORMA_{aksi}:{ctx['user_id']}:{pid}:{kunci}"
    sidik = hash_payload(isi)
    await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", f"IDEM:{ctx['tenant_id']}:{kp}")
    try:
        lama = await ambil_replay_klien(conn, ctx["tenant_id"], kp, sidik)
    except LookupError:
        raise HTTPException(status_code=409, detail={
            "code": "IDEMPOTENCY_KEY_REUSED",
            "message": "Idempotency-Key sudah dipakai untuk aksi proforma dengan isi berbeda"})
    if lama is not None and response is not None:
        response.headers["X-Idempotent-Replay"] = "true"
    return kp, sidik, lama


async def _simpan_idem_aksi(conn, ctx: dict, kp, sidik, sumber: str, resp: dict, pid) -> dict:
    from fastapi.encoders import jsonable_encoder
    resp = jsonable_encoder(resp)
    if kp:
        await simpan_replay_klien(conn, ctx["tenant_id"], kp, sumber, sidik, resp, result_id=pid)
    return resp


async def _tulis_terbit(conn, ctx: dict, cur):
    """draft -> issued + riwayat, satu savepoint. SATU penulis untuk /issue DAN pratinjau (yang di-rollback)."""
    async with conn.transaction():
        row = await conn.fetchrow(
            """UPDATE proformas SET status = 'issued', issued_at = NOW()
               WHERE id = $1 AND tenant_id = $2 AND status = 'draft' RETURNING *""", cur["id"], ctx["tenant_id"])
        if not row:
            raise HTTPException(status_code=409, detail="Proforma sudah berubah status.")
        # Riwayat SO: issued_at tak punya kolom aktor -> audit_logs, tx yang sama (Law 12)
        await catat_riwayat(
            conn, ctx["tenant_id"], "proformas", cur["id"], row["proforma_number"], "PROFORMA_ISSUED",
            ctx.get("user_id"), f"Proforma {row['proforma_number'] or ''} diterbitkan".replace("  ", " "),
            {"sales_order_id": str(cur["sales_order_id"])}, source="api:proformas.issue",
        )
    return row


async def _tulis_batal(conn, ctx: dict, cur, alasan: str):
    """-> cancelled + riwayat, satu savepoint. SATU penulis untuk /cancel DAN pratinjau (yang di-rollback)."""
    async with conn.transaction():
        row = await conn.fetchrow(
            """UPDATE proformas SET status = 'cancelled', cancelled_at = NOW(), cancelled_reason = $3
               WHERE id = $1 AND tenant_id = $2 AND status <> 'cancelled' RETURNING *""",
            cur["id"], ctx["tenant_id"], alasan)
        if not row:
            raise HTTPException(status_code=409, detail="Proforma sudah berubah status.")
        await catat_riwayat(
            conn, ctx["tenant_id"], "proformas", cur["id"], row["proforma_number"], "PROFORMA_CANCELLED",
            ctx.get("user_id"),
            f"Proforma {row['proforma_number'] or ''} dibatalkan".replace("  ", " ") + (f": {alasan}" if alasan else ""),
            {"reason": alasan, "sales_order_id": str(cur["sales_order_id"])}, source="api:proformas.cancel",
        )
    return row


class _BatalkanPratinjau(Exception):
    def __init__(self, data):
        self.data = data


async def _pratinjau(ctx: dict, pid, aksi: str, alasan=None) -> dict:
    """Penentu YANG SAMA + (bila tak terblok) PENULIS YANG SAMA, lalu ROLLBACK. Dampak 'sesudah' = rincian tagih
    NYATA sesudah tulis: atribusi uang muka bisa berpindah (uang muka tak tertaut dicocokkan nominal ke proforma yang
    baru terbit; terukur 4 Okt PRO-2609-0028: di luar tagihan 400rb -> 0), jadi tak bisa dihitung dengan menambah."""
    pool = await get_pool()
    try:
        async with pool.acquire() as conn:
            async with conn.transaction():
                cur = await _kunci_proforma(conn, ctx, pid)
                r = await (_rencana_terbit(conn, ctx, cur) if aksi == "issue" else _rencana_batal(conn, ctx, cur, alasan))
                r["impact"]["after"] = r["impact"]["before"]
                if not r["blocks"]:
                    if aksi == "issue":
                        await _tulis_terbit(conn, ctx, cur)
                    else:
                        await _tulis_batal(conn, ctx, cur, alasan)
                    total = _f(r["order"]["total_amount"]) or 0.0
                    r["impact"]["after"] = _ringkas_tagih(
                        await rincian_tagih(conn, ctx["tenant_id"], cur["sales_order_id"], total))
                raise _BatalkanPratinjau(_bentuk_pratinjau(cur, r))
    except _BatalkanPratinjau as b:
        return b.data


class PratinjauBatalProforma(BaseModel):
    reason: Optional[str] = None  # opsional DI PRATINJAU supaya "alasan wajib" tampil sebagai blok, bukan 422 skema


@router.post("/{proforma_id}/issue/preview")
async def preview_issue_proforma(request: Request, proforma_id: str):
    """Pratinjau terbit: penentu + penulis YANG SAMA dengan /issue lalu ROLLBACK (nol tulis), semua blok + dampak
    plafon tagih SO sebelum/sesudah (sesudah = nyata, lihat _pratinjau)."""
    ctx = get_user_context(request)
    return {"success": True, "data": await _pratinjau(ctx, _uuid_or_404(proforma_id), "issue")}


@router.post("/{proforma_id}/cancel/preview")
async def preview_cancel_proforma(request: Request, proforma_id: str, body: Optional[PratinjauBatalProforma] = None):
    """Pratinjau batal: penentu + penulis YANG SAMA dengan /cancel lalu ROLLBACK, semua blok (sudah batal, alasan
    wajib, uang muka tertaut) + uang muka tertaut + dampak plafon tagih SO sebelum/sesudah."""
    ctx = get_user_context(request)
    return {"success": True, "data": await _pratinjau(ctx, _uuid_or_404(proforma_id), "cancel",
                                                       body.reason if body else None)}


@router.post("/{proforma_id}/issue")
async def issue_proforma(request: Request, proforma_id: str, response: _Response = None):
    """draft -> issued. NON-POSTING: tidak ada jurnal yang dibuat. Penentu = _rencana_terbit (sama dengan pratinjau).
    X-Idempotency-Key opsional: retry sesudah balasan hilang = respons pertama (bukan 400 'bukan draf')."""
    try:
        ctx = get_user_context(request)
        pid = _uuid_or_404(proforma_id)
        pool = await get_pool()

        async with pool.acquire() as conn:
            async with conn.transaction():
                kp, sd, lama = await _idem_aksi(conn, ctx, request, "ISSUE", pid, {"proforma_id": str(pid)}, response)
                if lama is not None:
                    return lama
                cur = await _kunci_proforma(conn, ctx, pid)
                r = await _rencana_terbit(conn, ctx, cur)
                _angkat_blok_pertama(r)
                order = r["order"]

                row = await _tulis_terbit(conn, ctx, cur)

                paid, paid_breakdown = await terbayar_satu(conn, ctx["tenant_id"], row)
                return await _simpan_idem_aksi(conn, ctx, kp, sd, "PROFORMA_ISSUE", {
                    "success": True,
                    "data": serialize_proforma(row, order["order_number"], paid, paid_breakdown),
                }, pid)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error issuing proforma {proforma_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to issue proforma")


@router.post("/{proforma_id}/cancel")
async def cancel_proforma(request: Request, proforma_id: str, body: CancelProformaRequest,
                          response: _Response = None):
    """Batalkan proforma. DITOLAK bila sudah ada deposit yang menunjuk padanya. Penentu = _rencana_batal (sama dengan
    pratinjau). 4 Okt 2026: SELURUHNYA satu transaksi + kunci PROFORMA_SO + FOR UPDATE baris SO (mutex dengan BUAT
    uang muka, yang mengunci baris SO yang sama) -- dulu cek terbayar di LUAR transaksi: uang muka yang menaut proforma
    bisa masuk di antara cek dan UPDATE. X-Idempotency-Key opsional (pola W0)."""
    try:
        ctx = get_user_context(request)
        pid = _uuid_or_404(proforma_id)
        pool = await get_pool()

        async with pool.acquire() as conn:
            async with conn.transaction():
                kp, sd, lama = await _idem_aksi(conn, ctx, request, "CANCEL", pid, body.model_dump(mode="json"), response)
                if lama is not None:
                    return lama
                cur = await _kunci_proforma(conn, ctx, pid)
                await conn.execute("SELECT 1 FROM sales_orders WHERE id = $1 AND tenant_id = $2 FOR UPDATE",
                                   cur["sales_order_id"], ctx["tenant_id"])
                r = await _rencana_batal(conn, ctx, cur, body.reason)
                _angkat_blok_pertama(r)
                order = r["order"]

                row = await _tulis_batal(conn, ctx, cur, body.reason)
                return await _simpan_idem_aksi(conn, ctx, kp, sd, "PROFORMA_CANCEL", {
                    "success": True,
                    "data": serialize_proforma(row, order["order_number"], 0.0),
                }, pid)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error cancelling proforma {proforma_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to cancel proforma")


# ============================================================================
# PDF
# ============================================================================


# Baris konteks PDF proforma -- SATU SQL untuk rute /pdf dan render dokumen (P3 SO-dokumen).
SQL_PDF_PROFORMA = """
                SELECT p.*, so.order_number, so.order_date, so.total_amount AS order_total_amount,
                       so.subtotal AS order_subtotal, so.discount_amount AS order_discount,
                       so.shipping_amount AS order_shipping, so.tax_amount AS order_tax
                FROM proformas p
                LEFT JOIN sales_orders so
                       ON so.id = p.sales_order_id AND so.tenant_id = p.tenant_id
                WHERE p.id = $1 AND p.tenant_id = $2
                """


async def muat_pdf_proforma_id(conn, ctx, pid) -> dict:
    """Pemuat PDF proforma berdasar id (render dokumen P3): baris SQL_PDF_PROFORMA + muat_pdf_proforma; 404 bila tak ada."""
    row = await conn.fetchrow(SQL_PDF_PROFORMA, pid, ctx["tenant_id"])
    if not row:
        raise HTTPException(status_code=404, detail="Proforma not found")
    return await muat_pdf_proforma(conn, ctx, row)


async def muat_pdf_proforma(conn, ctx, row) -> dict:
    """Konteks PDF proforma dari baris SQL_PDF_PROFORMA (P3 SO-dokumen: SATU sumber). Dipindah VERBATIM
    dari get_proforma_pdf. -> {proforma_data, tenant_info}."""
    paid, paid_breakdown = await terbayar_satu(conn, ctx["tenant_id"], row)

    # Rincian item Sales Order untuk tabel Keterangan.
    # `sales_order_items` TIDAK punya kolom `deleted_at` (diukur
    # 2026-09-03 lewat information_schema), jadi tak ada saringan
    # soft-delete yang bisa dipasang -- baris yang ada adalah baris
    # yang berlaku.
    _order_items = []
    if row["sales_order_id"]:
        _oi = await conn.fetch(
            """
            SELECT description, quantity, unit, unit_price, line_total, tax_amount
            FROM sales_order_items
            WHERE sales_order_id = $1
            ORDER BY sort_order, description
            """,
            row["sales_order_id"],
        )
        _order_items = [
            {
                "description": r["description"],
                "quantity": _f(r["quantity"]),
                "unit": r["unit"],
                "unit_price": _f(r["unit_price"]),
                # 30 Sep: jumlah baris = NETO (line_total SO = neto + PPN baris). Dasar SAMA dengan
                # subtotal SO (Σ neto) -> baris Subtotal/Diskon/Ongkir/PPN di bawahnya tak menghitung
                # PPN dua kali. Tanpa PPN (grapgrap) = line_total, tak berubah.
                "line_total": _f(r["line_total"] - (r["tax_amount"] or 0)),
            }
            for r in _oi
        ]

    tenant_row = await conn.fetchrow(
        'SELECT display_name, address, phone, logo_url FROM "Tenant" WHERE id = $1',
        ctx["tenant_id"],
    )
    if tenant_row:
        tenant_info = {
            "name": tenant_row["display_name"],
            "address": tenant_row["address"],
            "phone": tenant_row["phone"],
            "logo_url": tenant_row["logo_url"],
        }
    else:
        tenant_info = {
            "name": ctx["tenant_id"],
            "address": None,
            "phone": None,
            "logo_url": None,
        }

    _logo_data = None
    _logo_filename = tenant_info.get("logo_url")
    if _logo_filename:
        _logo_path = (
            _Path(__file__).parent.parent / "static" / "logos" / _logo_filename
        )
        if _logo_path.exists():
            with open(_logo_path, "rb") as _lf:
                _logo_data = (
                    "data:image/png;base64,"
                    + base64.b64encode(_lf.read()).decode()
                )
    tenant_info["logo_data"] = _logo_data

    amount = _f(row["amount"]) or 0.0
    # Tanggal lunas JUJUR: bila uang muka yang TERTAUT ke proforma ini (proforma_id) sendiri menutupinya = tanggal
    # uang muka terakhir itu. Lunas dari kolam pesanan (pembayaran faktur / uang muka SO tanpa tautan) tak punya
    # satu tanggal yang benar -> None (stempel tanpa tanggal; tak mengarang).
    _tanggal_lunas = None
    _tertaut = await conn.fetchrow(
        """SELECT COALESCE(SUM(amount), 0) AS jml, MAX(deposit_date) AS tgl FROM customer_deposits
           WHERE tenant_id = $1 AND proforma_id = $2 AND status <> 'void' AND journal_id IS NOT NULL""",
        ctx["tenant_id"], row["id"],
    )
    if _tertaut and amount > 0 and float(_tertaut["jml"] or 0) >= amount and _tertaut["tgl"]:
        _tanggal_lunas = _tertaut["tgl"].isoformat()
    # Celah 1 (MASTER 28 Sep): angka ringkasan dari proforma LAIN & uang muka, rumus sama dengan plafon.
    _total_so = _f(row["order_total_amount"]) or 0.0
    _billed_before = await issued_total_for_order(conn, ctx["tenant_id"], row["sales_order_id"], exclude_id=row["id"]) \
        if row["sales_order_id"] else 0.0
    _atr = (await muat_atribusi(conn, ctx["tenant_id"], [row["sales_order_id"]]))[row["sales_order_id"]] \
        if row["sales_order_id"] else None
    _milik_ini = (_atr["per_proforma"].get(row["id"], {}) if _atr else {})
    # diterima SEBELUM tagihan ini = semua uang muka SO kecuali yang diatribusikan ke proforma INI (tautan/cocok)
    _received_before = float(_atr["diterima"] - _milik_ini.get("tertaut", 0) - _milik_ini.get("dicocokkan", 0)) \
        if _atr else 0.0
    _tak_tertagih = float(_atr["tak_tertagih"]) if _atr else 0.0
    from ..services import faktur_cetak as _fc
    _rek_cetak = await _fc.muat_rekening(conn, ctx["tenant_id"])
    proforma_data = {
        "id": str(row["id"]),
        "proforma_number": row["proforma_number"],
        "proforma_date": row["proforma_date"].isoformat()
        if row["proforma_date"]
        else None,
        "due_date": row["due_date"].isoformat() if row["due_date"] else None,
        "sales_order_number": row["order_number"],
        "sales_order_date": row["order_date"].isoformat()
        if row["order_date"]
        else None,
        "order_total_amount": _f(row["order_total_amount"]),
        # 30 Sep (pemilik, PRO-2609-0069): komposisi Nilai Pesanan dari kolom SO yang SAMA dengan detail
        # SO (tidak dihitung ulang): Subtotal (neto) -> Diskon -> Ongkos kirim -> PPN (baris + ongkir) -> Nilai.
        "order_subtotal": _f(row["order_subtotal"]),
        "order_discount": _f(row["order_discount"]),
        "order_shipping": _f(row["order_shipping"]),
        "order_tax": _f(row["order_tax"]),
        "customer_name": row["customer_name"],
        "purpose": row["purpose"],
        "percent_of_order": _f(row["percent_of_order"]),
        "amount": amount,
        "paid_amount": paid,
        "outstanding_amount": round(amount - paid, 2),
        # ── angka turunan untuk ringkasan kanan ──
        # Persen ditampilkan HANYA bila nilai SO > 0; tanpa penjaga itu
        # proforma tanpa SO membagi dengan nol.
        "order_items": _order_items,
        "dp_percent_display": (
            int(round(amount / _f(row["order_total_amount"]) * 100))
            if _f(row["order_total_amount"])
            else None
        ),
        # Celah 1: sisa SESUDAH tagihan ini = max(0, total - max(ditagih_sebelum + ini, diterima_sebelum)).
        # Dulu total - amount INI saja -> salah untuk PELUNASAN dan uang muka kedua.
        "billed_before": round(_billed_before, 2),
        "received_before": round(_received_before, 2),
        "remaining_after_this": (
            sisa_bisa_ditagih(_total_so, _billed_before + amount, _tak_tertagih) if _total_so else None
        ),
        # Baris "Sudah Dibayar"/"Sisa Tagihan Ini" HANYA saat proforma
        # dibayar SEBAGIAN. Belum dibayar sama sekali -> nol baris sisa.
        "is_partially_paid": bool(0 < paid < amount),
        # 1 Okt 2026 (MASTER/pemilik, pola faktur 27 Sep "Lunas: JANGAN Rp 0 sebagai angka besar"): proforma terbit
        # yang LUNAS -> angka besar = nominal tagihan + stempel LUNAS; tanggal hanya bila jujur diketahui.
        "is_paid": bool(row["status"] == "issued" and amount > 0 and paid >= amount),
        "paid_date": _tanggal_lunas,
        "currency": row["currency"],
        "terms": row["terms"],
        "notes": row["notes"],
        "payment_bank_name": row["payment_bank_name"],
        "payment_account_number": row["payment_account_number"],
        "payment_account_holder": row["payment_account_holder"],
        "rekening_pemilik_cetak": _fc.pemilik_dari(
            _rek_cetak, row["payment_bank_name"], row["payment_account_number"], row["payment_account_holder"]),
        "status": row["status"],
        # tanda DIBATALKAN pada proforma yang dibatalkan (pdf_service._tanda_batal)
        "cancelled_at": row["cancelled_at"],
        "cancelled_reason": row["cancelled_reason"],
    }
    from ..services.kode_order import label_cetak
    proforma_data.update(await label_cetak(conn, ctx["tenant_id"], row["sales_order_id"]))
    return {"proforma_data": proforma_data, "tenant_info": tenant_info}


@router.get("/{proforma_id}/pdf")
async def get_proforma_pdf(
    request: Request,
    proforma_id: str,
    format: Literal["url", "inline"] = Query(
        "inline",
        description="'inline' (bawaan, perilaku lama) = byte PDF; 'url' = path relatif gateway ke PDF ini (pola Unit 2 faktur)",
    ),
):
    """PDF tagihan uang muka. BUKAN faktur pajak. Draf bertanda DRAF, batal bertanda DIBATALKAN (26 Sep 2026)."""
    try:
        ctx = get_user_context(request)
        pid = _uuid_or_404(proforma_id)
        pool = await get_pool()

        async with pool.acquire() as conn:
            row = await conn.fetchrow(SQL_PDF_PROFORMA, pid, ctx["tenant_id"])
            if not row:
                raise HTTPException(status_code=404, detail="Proforma not found")
            if format == "url":
                # Pola Unit 2 (utils/pdf_url): path relatif gateway, dirender saat diunduh (izin + pagar tenant
                # berlaku tiap unduhan), tanpa salinan di MinIO, tanpa kedaluwarsa.
                from ..utils.pdf_url import respons_pdf_url
                return respons_pdf_url("proformas", pid, f"{row['proforma_number'] or 'proforma'}.pdf")

            _m = await muat_pdf_proforma(conn, ctx, row)
        proforma_data, tenant_info = _m["proforma_data"], _m["tenant_info"]

        from ..services.pdf_service import get_pdf_service

        pdf_bytes = get_pdf_service().generate_proforma_pdf(proforma_data, tenant_info)

        proforma_num = row['proforma_number'] or str(proforma_id)[:8]
        from ..utils.content_disposition import pdf_content_disposition, sanitize_filename
        filename = sanitize_filename(proforma_num) + ".pdf"
        return StreamingResponse(
            BytesIO(pdf_bytes),
            media_type="application/pdf",
            headers={
                "Content-Disposition": pdf_content_disposition(proforma_num),
                "Cache-Control": "no-store",
            },
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error generating PDF for proforma {proforma_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to generate PDF")
