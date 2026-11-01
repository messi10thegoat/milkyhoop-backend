"""Agregat daftar Pesanan Pembelian untuk jawaban deterministik Workspace (#4, 1 Nov 2026) -- cermin Q-012 SO (services/so_agregat).

GET /api/purchase-orders/aggregate?q=unbilled|top_vendors|vendor_status|receive_window

Putusan MASTER 1 Nov: "belum ditagih" = SATU definisi PO yang sudah ada, PD.fakta_po(...)["sisa_ditagih"] (sama dengan kolom
Sisa daftar/detail; quantity_billed turunan bill_items bertaut, tagihan DRAF dihitung SUDAH ditagih). Bagian draf ditampilkan
terpisah (draft_bill_amount) supaya FE bisa menyebutnya. Asimetri dengan SO (faktur draf = belum ditagih) = tiket terpisah.
PO adalah komitmen (belum AP), jadi bukan uang jurnal. Validasi parameter (400) memakai pembantu so_agregat apa adanya (paritas).
"""
from fastapi import HTTPException

from . import po_daftar as PD
from .so_agregat import BATAS_BARIS, _400, _f, _uuid, batas, rentang

JENIS = ("unbilled", "top_vendors", "vendor_status", "receive_window")
AKTIF_TIDAK = ("draft", "cancelled", "closed")   # PO yang masih "berjalan" = selain ini
NOL = PD.NOL

_NAMA = "COALESCE(v.name, po.vendor_name)"
_JOIN_VENDOR = "LEFT JOIN vendors v ON v.id = po.vendor_id AND v.tenant_id = po.tenant_id"
# Porsi nilai PO yang tertagih oleh tagihan DRAF -- tautan & pembulatan SAMA dengan SQL_AGREGAT.amount_billed (bill_items bertaut).
_SQL_DRAF = """COALESCE((SELECT SUM(ROUND(d.qty * i.total / NULLIF(i.quantity, 0), 2)) FROM purchase_order_items i
                   JOIN LATERAL (SELECT COALESCE(SUM(bi.quantity), 0) AS qty FROM bill_items bi JOIN bills b ON b.id = bi.bill_id
                                 WHERE bi.purchase_order_item_id = i.id AND b.tenant_id = po.tenant_id
                                   AND COALESCE(b.status_v2, b.status) = 'draft') d ON true
                   WHERE i.po_id = po.id), 0)"""


async def _sisa(conn, tenant_id: str, ids: list) -> dict:
    """{po_id: Decimal sisa ditagih} -- fungsi YANG SAMA dengan baris daftar/detail (PD.fakta_po)."""
    if not ids:
        return {}
    hari = await PD.tanggal_hari_ini(conn, tenant_id)
    return {k: v["sisa_ditagih"] for k, v in (await PD.fakta_po(conn, tenant_id, ids, hari)).items()}


async def unbilled(conn, tenant_id: str) -> dict:
    rows = await conn.fetch(
        f"""SELECT po.id, po.po_number, po.vendor_id, {_NAMA} AS vendor_name, po.po_date, po.status, po.total_amount,
                   {_SQL_DRAF} AS draft_bill_amount
            FROM purchase_orders po {_JOIN_VENDOR}
            WHERE po.tenant_id = $1 AND po.status <> ALL($2::text[])""",
        tenant_id, list(AKTIF_TIDAK),
    )
    sisa = await _sisa(conn, tenant_id, [r["id"] for r in rows])
    isi = sorted((r for r in rows if sisa.get(r["id"], NOL) > NOL), key=lambda r: (-sisa[r["id"]], r["po_number"]))
    return {
        "total": _f(sum((sisa[r["id"]] for r in isi), NOL)),
        "count": len(isi),
        "truncated": len(isi) > BATAS_BARIS,
        "rows": [{
            "id": str(r["id"]), "po_number": r["po_number"],
            "vendor_id": str(r["vendor_id"]) if r["vendor_id"] else None,
            "vendor_name": r["vendor_name"], "po_date": r["po_date"].isoformat(),
            "status": r["status"], "total_amount": _f(r["total_amount"]),
            "unbilled_amount": _f(sisa[r["id"]]),
            "draft_bill_amount": _f(r["draft_bill_amount"]),
        } for r in isi[:BATAS_BARIS]],
    }


async def top_vendors(conn, tenant_id: str, dari, sampai, limit) -> dict:
    a, b = rentang(dari, sampai)
    n = batas(limit)
    rows = await conn.fetch(
        f"""SELECT po.vendor_id, MAX({_NAMA}) AS vendor_name, SUM(po.total_amount) AS total,
                   COUNT(*) AS count, array_agg(po.id ORDER BY po.po_date, po.po_number) AS po_ids
            FROM purchase_orders po {_JOIN_VENDOR}
            WHERE po.tenant_id = $1 AND po.status NOT IN ('draft', 'cancelled')
              AND po.po_date BETWEEN $2 AND $3
            GROUP BY po.vendor_id
            ORDER BY SUM(po.total_amount) DESC, MAX({_NAMA})
            LIMIT $4""",
        tenant_id, a, b, n,
    )
    return {
        "period": {"from": a.isoformat(), "to": b.isoformat()},
        "rows": [{
            "vendor_id": str(r["vendor_id"]) if r["vendor_id"] else None,
            "vendor_name": r["vendor_name"], "total": _f(r["total"]), "count": r["count"],
            "po_ids": [str(i) for i in r["po_ids"]],
        } for r in rows],
    }


async def vendor_status(conn, tenant_id: str, vendor_id) -> dict:
    vid = _uuid(vendor_id, "vendor_id")
    nama = await conn.fetchrow("SELECT name FROM vendors WHERE id = $1 AND tenant_id = $2", vid, tenant_id)
    if nama is None:
        raise HTTPException(status_code=404, detail="Vendor tidak ditemukan.")
    rows = await conn.fetch(
        """SELECT po.id, po.status, po.total_amount FROM purchase_orders po
           WHERE po.tenant_id = $1 AND po.vendor_id = $2
           ORDER BY po.po_date, po.po_number""",
        tenant_id, vid,
    )
    per = {}
    for r in rows:
        s = per.setdefault(r["status"], {"status": r["status"], "count": 0, "total": NOL})
        s["count"] += 1
        s["total"] += r["total_amount"] or NOL
    sisa = await _sisa(conn, tenant_id, [r["id"] for r in rows if r["status"] not in AKTIF_TIDAK])
    return {
        "vendor_id": str(vid), "vendor_name": nama["name"],
        "by_status": [{**s, "total": _f(s["total"])} for s in sorted(per.values(), key=lambda s: s["status"])],
        "unbilled_total": _f(sum(sisa.values(), NOL)),
        "po_ids": [str(r["id"]) for r in rows],
    }


async def receive_window(conn, tenant_id: str, dari, sampai) -> dict:
    a, b = rentang(dari, sampai)
    rows = await conn.fetch(
        f"""SELECT po.id, po.po_number, {_NAMA} AS vendor_name, po.expected_date, po.total_amount, po.status
            FROM purchase_orders po {_JOIN_VENDOR}
            WHERE po.tenant_id = $1 AND po.status <> ALL($2::text[])
              AND po.expected_date BETWEEN $3 AND $4
            ORDER BY po.expected_date, po.po_number""",
        tenant_id, list(AKTIF_TIDAK), a, b,
    )
    tanpa = await conn.fetchval(
        """SELECT COUNT(*) FROM purchase_orders po
           WHERE po.tenant_id = $1 AND po.status <> ALL($2::text[]) AND po.expected_date IS NULL""",
        tenant_id, list(AKTIF_TIDAK),
    )
    return {
        "period": {"from": a.isoformat(), "to": b.isoformat()},
        "count": len(rows),
        "total": _f(sum((r["total_amount"] or NOL for r in rows), NOL)),
        "truncated": len(rows) > BATAS_BARIS,
        "without_expected_date_count": tanpa or 0,
        "rows": [{
            "id": str(r["id"]), "po_number": r["po_number"], "vendor_name": r["vendor_name"],
            "expected_date": r["expected_date"].isoformat(),
            "total_amount": _f(r["total_amount"]), "status": r["status"],
        } for r in rows[:BATAS_BARIS]],
    }


async def agregat(conn, tenant_id: str, q, dari=None, sampai=None, vendor_id=None, limit=None) -> dict:
    if q not in JENIS:
        _400(f"Parameter 'q' harus salah satu: {', '.join(JENIS)}.")
    if q == "unbilled":
        data = await unbilled(conn, tenant_id)
    elif q == "top_vendors":
        data = await top_vendors(conn, tenant_id, dari, sampai, limit)
    elif q == "vendor_status":
        data = await vendor_status(conn, tenant_id, vendor_id)
    else:
        data = await receive_window(conn, tenant_id, dari, sampai)
    return {"success": True, "q": q, "data": data}
