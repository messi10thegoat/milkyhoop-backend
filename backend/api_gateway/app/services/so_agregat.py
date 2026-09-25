"""Agregat daftar Pesanan Penjualan untuk jawaban deterministik Workspace (Q-012, 25 Sep 2026).

GET /api/sales-orders/aggregate?q=uninvoiced|top_customers|customer_status|ship_window

Kenapa di server: prototype FE menjumlah baris daftar (maks 100 dimuat) dan memakai "Σ total SO
Dikonfirmasi" sebagai "belum ditagih". Diukur 25 Sep: grapgrap belum-ditagih nyata 50.285.000
(confirmed 48,66 jt + SO-2609-0006 berstatus 'invoiced' yang fakturnya masih DRAF), sedangkan
/summary.pending_invoice_value (berbasis status) = 0. Maka:
- uang "belum ditagih" = proforma_terbayar.belum_ditagih (turunan JURNAL), bukan status SO;
- faktur DRAF dihitung belum ditagih; jumlahnya ditampilkan terpisah (draft_invoice_amount);
- nama pelanggan = nama KINI di master customers (salinan di SO basi sesudah rename).
Putusan MASTER 25 Sep: draf faktur = belum ditagih; top_customers tanpa draf SO; minggu dikirim FE.
"""
import uuid
from datetime import date
from fastapi import HTTPException

from .proforma_terbayar import NOL, belum_ditagih, ringkasan_pesanan

JENIS = ("uninvoiced", "top_customers", "customer_status", "ship_window")
AKTIF_TIDAK = ("draft", "cancelled", "completed")   # SO yang masih "berjalan" = selain ini
BATAS_BARIS = 50
RENTANG_MAKS_HARI = 366

_NAMA = "COALESCE(c.nama, so.customer_name)"
_JOIN_PELANGGAN = "LEFT JOIN customers c ON c.id = so.customer_id AND c.tenant_id = so.tenant_id"


def _400(pesan: str):
    raise HTTPException(status_code=400, detail=pesan)


def _tanggal(nilai, nama: str) -> date:
    if not nilai:
        _400(f"Parameter '{nama}' wajib (YYYY-MM-DD).")
    try:
        return date.fromisoformat(str(nilai))
    except ValueError:
        _400(f"Parameter '{nama}' harus tanggal YYYY-MM-DD, bukan {nilai!r}.")


def rentang(dari, sampai) -> tuple:
    a, b = _tanggal(dari, "from"), _tanggal(sampai, "to")
    if a > b:
        _400("Parameter 'from' harus sebelum atau sama dengan 'to'.")
    if (b - a).days + 1 > RENTANG_MAKS_HARI:
        _400(f"Rentang maksimal {RENTANG_MAKS_HARI} hari.")
    return a, b


def batas(limit, bawaan: int = 3, maks: int = 10) -> int:
    if limit in (None, ""):
        return bawaan
    try:
        n = int(str(limit))
    except ValueError:
        n = 0
    if not 1 <= n <= maks:
        _400(f"Parameter 'limit' harus bilangan 1–{maks}.")
    return n


def _uuid(nilai, nama: str) -> uuid.UUID:
    if not nilai:
        _400(f"Parameter '{nama}' wajib.")
    try:
        return uuid.UUID(str(nilai))
    except ValueError:
        _400(f"Parameter '{nama}' bukan UUID yang sah.")


def _f(v) -> float:
    return float(v if v is not None else NOL)


async def _belum_ditagih_per_so(conn, tenant_id: str, rows: list) -> dict:
    """{so_id: Decimal belum ditagih} untuk baris SO (id, total_amount) — satu definisi Q-011."""
    ring = await ringkasan_pesanan(conn, tenant_id, [r["id"] for r in rows])
    return {r["id"]: belum_ditagih(r["total_amount"], ring[r["id"]]) for r in rows}


async def uninvoiced(conn, tenant_id: str) -> dict:
    rows = await conn.fetch(
        f"""SELECT so.id, so.order_number, so.customer_id, {_NAMA} AS customer_name, so.order_date,
                   so.status, so.total_amount,
                   COALESCE((SELECT SUM(si.total_amount) FROM sales_invoices si
                             WHERE si.tenant_id = so.tenant_id AND si.sales_order_id = so.id
                               AND si.status = 'draft'), 0) AS draft_invoice_amount
            FROM sales_orders so {_JOIN_PELANGGAN}
            WHERE so.tenant_id = $1 AND so.status <> ALL($2::text[])""",
        tenant_id, list(AKTIF_TIDAK),
    )
    belum = await _belum_ditagih_per_so(conn, tenant_id, rows)
    isi = sorted((r for r in rows if belum[r["id"]] > NOL),
                 key=lambda r: (-belum[r["id"]], r["order_number"]))
    return {
        "total": _f(sum((belum[r["id"]] for r in isi), NOL)),
        "count": len(isi),
        "truncated": len(isi) > BATAS_BARIS,
        "rows": [{
            "id": str(r["id"]), "order_number": r["order_number"],
            "customer_id": str(r["customer_id"]) if r["customer_id"] else None,
            "customer_name": r["customer_name"], "order_date": r["order_date"].isoformat(),
            "status": r["status"], "total_amount": _f(r["total_amount"]),
            "uninvoiced_amount": _f(belum[r["id"]]),
            "draft_invoice_amount": _f(r["draft_invoice_amount"]),
        } for r in isi[:BATAS_BARIS]],
    }


async def top_customers(conn, tenant_id: str, dari, sampai, limit) -> dict:
    a, b = rentang(dari, sampai)
    n = batas(limit)
    rows = await conn.fetch(
        f"""SELECT so.customer_id, MAX({_NAMA}) AS customer_name, SUM(so.total_amount) AS total,
                   COUNT(*) AS count, array_agg(so.id ORDER BY so.order_date, so.order_number) AS order_ids
            FROM sales_orders so {_JOIN_PELANGGAN}
            WHERE so.tenant_id = $1 AND so.status NOT IN ('draft', 'cancelled')
              AND so.order_date BETWEEN $2 AND $3
            GROUP BY so.customer_id
            ORDER BY SUM(so.total_amount) DESC, MAX({_NAMA})
            LIMIT $4""",
        tenant_id, a, b, n,
    )
    return {
        "period": {"from": a.isoformat(), "to": b.isoformat()},
        "rows": [{
            "customer_id": str(r["customer_id"]) if r["customer_id"] else None,
            "customer_name": r["customer_name"], "total": _f(r["total"]), "count": r["count"],
            "order_ids": [str(i) for i in r["order_ids"]],
        } for r in rows],
    }


async def customer_status(conn, tenant_id: str, customer_id) -> dict:
    cid = _uuid(customer_id, "customer_id")
    nama = await conn.fetchrow(
        "SELECT nama FROM customers WHERE id = $1 AND tenant_id = $2", cid, tenant_id)
    if nama is None:
        raise HTTPException(status_code=404, detail="Pelanggan tidak ditemukan.")
    rows = await conn.fetch(
        """SELECT so.id, so.status, so.total_amount FROM sales_orders so
           WHERE so.tenant_id = $1 AND so.customer_id = $2
           ORDER BY so.order_date, so.order_number""",
        tenant_id, cid,
    )
    per = {}
    for r in rows:
        s = per.setdefault(r["status"], {"status": r["status"], "count": 0, "total": NOL})
        s["count"] += 1
        s["total"] += r["total_amount"] or NOL
    aktif = [r for r in rows if r["status"] not in AKTIF_TIDAK]
    belum = await _belum_ditagih_per_so(conn, tenant_id, aktif)
    return {
        "customer_id": str(cid), "customer_name": nama["nama"],
        "by_status": [{**s, "total": _f(s["total"])} for s in sorted(per.values(), key=lambda s: s["status"])],
        "uninvoiced_total": _f(sum(belum.values(), NOL)),
        "order_ids": [str(r["id"]) for r in rows],
    }


async def ship_window(conn, tenant_id: str, dari, sampai) -> dict:
    a, b = rentang(dari, sampai)
    rows = await conn.fetch(
        f"""SELECT so.id, so.order_number, {_NAMA} AS customer_name, so.expected_ship_date,
                   so.total_amount, so.status
            FROM sales_orders so {_JOIN_PELANGGAN}
            WHERE so.tenant_id = $1 AND so.status <> ALL($2::text[])
              AND so.expected_ship_date BETWEEN $3 AND $4
            ORDER BY so.expected_ship_date, so.order_number""",
        tenant_id, list(AKTIF_TIDAK), a, b,
    )
    tanpa = await conn.fetchval(
        """SELECT COUNT(*) FROM sales_orders so
           WHERE so.tenant_id = $1 AND so.status <> ALL($2::text[]) AND so.expected_ship_date IS NULL""",
        tenant_id, list(AKTIF_TIDAK),
    )
    return {
        "period": {"from": a.isoformat(), "to": b.isoformat()},
        "count": len(rows),
        "total": _f(sum((r["total_amount"] or NOL for r in rows), NOL)),
        "truncated": len(rows) > BATAS_BARIS,
        "without_ship_date_count": tanpa or 0,
        "rows": [{
            "id": str(r["id"]), "order_number": r["order_number"], "customer_name": r["customer_name"],
            "expected_ship_date": r["expected_ship_date"].isoformat(),
            "total_amount": _f(r["total_amount"]), "status": r["status"],
        } for r in rows[:BATAS_BARIS]],
    }


async def agregat(conn, tenant_id: str, q, dari=None, sampai=None, customer_id=None, limit=None) -> dict:
    if q not in JENIS:
        _400(f"Parameter 'q' harus salah satu: {', '.join(JENIS)}.")
    if q == "uninvoiced":
        data = await uninvoiced(conn, tenant_id)
    elif q == "top_customers":
        data = await top_customers(conn, tenant_id, dari, sampai, limit)
    elif q == "customer_status":
        data = await customer_status(conn, tenant_id, customer_id)
    else:
        data = await ship_window(conn, tenant_id, dari, sampai)
    return {"success": True, "q": q, "data": data}

