"""Riwayat KONTAK (Pelanggan / Vendor) -- B4 (4 Nov 2026). SATU sumber kejadian untuk GET /{id}/activity (bentuk lama) dan
GET /{id}/history (bentuk /sales-orders/{id}/history: {events:[{at, jenis, ringkas, aktor, dokumen}], omitted?}, terbaru dulu).

Sumber (dipindah apa adanya dari router /activity): *_activities + dokumen (faktur + penerimaan / tagihan + pembayaran keluar),
non-draf non-batal. Dokumen terkait disaring per izin BACA pemanggil (pola so_riwayat); modul tersaring -> `omitted`.
Tenant eksplisit di setiap kueri. Waktu = zona tenant; nominal = Rupiah baku (teks_galat.rp).
"""
from typing import Awaitable, Callable, Optional

MAKS = 2000  # batas kejadian yang dibaca sebelum saring izin

SQL = {
    "customer": {
        "hitung": """
            SELECT (SELECT COUNT(*) FROM customer_activities WHERE customer_id = $1 AND tenant_id = $2)
                 + (SELECT COUNT(*) FROM sales_invoices WHERE customer_id = $1 AND tenant_id = $2 AND status NOT IN ('draft', 'void'))
                 + (SELECT COUNT(*) FROM receive_payments WHERE customer_id = $1 AND tenant_id = $2 AND status NOT IN ('draft', 'void'))""",
        "baris": """
            SELECT id::text, type, description, details, actor_name, timestamp,
                   NULL as document_number, NULL::numeric as total, field_name, old_value, new_value
            FROM customer_activities WHERE customer_id = $1 AND tenant_id = $2
            UNION ALL
            SELECT si.id::text, 'sale' as type, 'Faktur penjualan ' || si.invoice_number as description,
                   'Total: ' || si.total_amount::text as details, NULL as actor_name, si.created_at as timestamp,
                   si.invoice_number as document_number, si.total_amount as total, NULL as field_name, NULL as old_value, NULL as new_value
            FROM sales_invoices si WHERE si.customer_id = $1 AND si.tenant_id = $2 AND si.status NOT IN ('draft', 'void')
            UNION ALL
            SELECT rp.id::text, 'payment' as type, 'Pembayaran ' || rp.payment_number as description,
                   'Total: ' || rp.total_amount::text as details, NULL as actor_name, rp.created_at as timestamp,
                   rp.payment_number as document_number, rp.total_amount as total, NULL as field_name, NULL as old_value, NULL as new_value
            FROM receive_payments rp WHERE rp.customer_id = $1 AND rp.tenant_id = $2 AND rp.status NOT IN ('draft', 'void')
            ORDER BY timestamp DESC LIMIT $3 OFFSET $4""",
        "dokumen": {"sale": ("sales_invoice", "sales_invoice"), "payment": ("receive_payment", "receive_payment")},
    },
    "vendor": {
        "hitung": """
            SELECT (SELECT COUNT(*) FROM vendor_activities WHERE vendor_id = $1 AND tenant_id = $2)
                 + (SELECT COUNT(*) FROM bills WHERE vendor_id = $1 AND tenant_id = $2 AND status_v2 NOT IN ('draft', 'void'))
                 + (SELECT COUNT(*) FROM bill_payments_v2 WHERE vendor_id = $1 AND tenant_id = $2 AND status NOT IN ('draft', 'void'))""",
        "baris": """
            SELECT id::text, type, description, details, actor_name, timestamp,
                   NULL as document_number, NULL::numeric as total, field_name, old_value, new_value
            FROM vendor_activities WHERE vendor_id = $1 AND tenant_id = $2
            UNION ALL
            SELECT b.id::text, 'bill' as type, 'Faktur pembelian ' || b.invoice_number as description,
                   'Total: ' || COALESCE(NULLIF(b.grand_total, 0), b.amount)::text as details, NULL as actor_name,
                   b.created_at as timestamp, b.invoice_number as document_number,
                   COALESCE(NULLIF(b.grand_total, 0), b.amount)::numeric as total, NULL as field_name, NULL as old_value, NULL as new_value
            FROM bills b WHERE b.vendor_id = $1 AND b.tenant_id = $2 AND b.status_v2 NOT IN ('draft', 'void')
            UNION ALL
            SELECT bp.id::text, 'payment' as type, 'Pembayaran ' || bp.payment_number as description,
                   'Total: ' || bp.total_amount::text as details, NULL as actor_name, bp.created_at as timestamp,
                   bp.payment_number as document_number, bp.total_amount::numeric as total, NULL as field_name, NULL as old_value,
                   NULL as new_value
            FROM bill_payments_v2 bp WHERE bp.vendor_id = $1 AND bp.tenant_id = $2 AND bp.status NOT IN ('draft', 'void')
            ORDER BY timestamp DESC LIMIT $3 OFFSET $4""",
        "dokumen": {"bill": ("bill", "purchase_invoice"), "payment": ("payment_out", "send_payment")},
    },
}


async def baris(conn, jenis: str, pihak_id, tid: str, limit: int, offset: int) -> tuple:
    """(rows, total) -- sumber BERSAMA /activity dan /history."""
    q = SQL[jenis]
    return await conn.fetch(q["baris"], pihak_id, tid, limit, offset), await conn.fetchval(q["hitung"], pihak_id, tid)


def ringkas(r) -> str:
    """Teks siap tampil: deskripsi + Rupiah baku (dulu 'Total: <numeric mentah>')."""
    from . import teks_galat as tg
    if r["total"] is not None:
        return f"{r['description']} · {tg.rp(r['total'])}"
    return r["description"] if not r.get("details") else f"{r['description']} · {r['details']}"


async def riwayat(conn, tid: str, jenis: str, pihak_id, boleh: Callable[[str], Awaitable[bool]], limit: int = 200) -> dict:
    from ..utils.tanggal_tenant import zona_tenant
    peta = SQL[jenis]["dokumen"]
    izin = {t: bool(await boleh(modul)) for t, (_, modul) in peta.items()}
    rows, _ = await baris(conn, jenis, pihak_id, tid, MAKS, 0)  # disaring izin sesudah diambil; batas atas tetap
    zona = await zona_tenant(conn, tid)
    ev = []
    for r in rows:
        tipe = r["type"]
        if tipe in peta and not izin[tipe]:
            continue
        ev.append({"at": r["timestamp"].astimezone(zona).isoformat() if r["timestamp"] else None, "jenis": tipe,
                   "ringkas": ringkas(r), "aktor": {"nama": r["actor_name"]} if r["actor_name"] else None,
                   "dokumen": ({"tipe": peta[tipe][0], "id": r["id"], "nomor": r["document_number"]} if tipe in peta else None)})
    out = {"events": ev[:limit]}
    omitted = sorted({modul for t, (_, modul) in peta.items() if not izin[t]})
    if omitted:
        out["omitted"] = omitted
    return out


def total_aktivitas(r) -> Optional[int]:
    """Bentuk lama /activity: total int (dipertahankan)."""
    return int(r["total"]) if r.get("total") is not None else None
