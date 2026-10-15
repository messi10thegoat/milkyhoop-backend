"""Lampiran keluarga untuk dokumen penjualan APA PUN, juga TANPA SO (LAMPIRAN-TANPA-SO, 15 Okt 2026, putusan MASTER).

GET /api/lampiran/{jenis}/{id}   jenis = sales_order | sales_invoice | receive_payment | customer_deposit | proforma | delivery | credit_note
                                        | quote (LAMPIRAN-NK-QUOTE 15 Okt)
  -> bentuk SAMA dengan GET /api/sales-orders/{id}/lampiran ({success, data[grup], jumlah}) + `sumber`:
     {"jenis", "id", "nomor", "mode": "keluarga_so" | "dokumen", "sales_order": {"id", "nomor"} | null}

Penentu (SATU predikat keluarga, services di routers/sales_order_lampiran):
  1. Dokumen harus ADA di tabel PEMILIK jenisnya, tenant JWT (penerimaan = receive_payments; id bill_payments_v2 -> 404; entity_type
     'payment' dipakai bersama dua modul).
  2. Calon SO dari tautan langsung dokumen; tiap calon DIVERIFIKASI lewat _SQL_ANGGOTA_KELUARGA (meta keluarga SO yang sama).
     Tepat SATU SO -> mode keluarga_so: hasil == /sales-orders/{so}/lampiran PERSIS (delegasi, bukan salinan).
  3. Nol SO (faktur langsung, penerimaan tanpa SO, ...) atau lebih dari satu (penerimaan dialokasikan ke faktur beberapa SO) ->
     mode dokumen: SQL keluarga yang SAMA dengan SO NULL + benih dokumen ini.
Izin rute = R modul jenisnya (ROUTE_PERMISSIONS); izin per GRUP = izin rute /download modul grup (susun_dengan_izin, sama dengan SO).
Baca saja; tenant eksplisit di setiap SQL (gateway BYPASSRLS).
"""
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request

from . import sales_order_lampiran as SL

router = APIRouter()

PENANDA_LAMPIRAN_DOKUMEN = "lampiran-dokumen-tanpa-so-w20261015"

# jenis -> (tabel pemilik, kolom nomor)
JENIS_DOKUMEN = {
    "sales_order": ("sales_orders", "order_number"),
    "sales_invoice": ("sales_invoices", "invoice_number"),
    "receive_payment": ("receive_payments", "payment_number"),
    "customer_deposit": ("customer_deposits", "deposit_number"),
    "proforma": ("proformas", "proforma_number"),
    "delivery": ("invoice_fulfillments", "fulfillment_number"),
    "credit_note": ("credit_notes", "credit_note_number"),
    "quote": ("quotes", "quote_number"),   # LAMPIRAN-NK-QUOTE 15 Okt
}

# Calon SO per jenis ($1 = id dokumen, $2 = tenant). Hanya USULAN: keanggotaan diputuskan _SQL_ANGGOTA_KELUARGA.
_DP_SO = """
    SELECT c1.sales_order_id FROM customer_deposits c1 WHERE c1.id = ANY({ids}) AND c1.tenant_id = $2
    UNION SELECT p1.sales_order_id FROM customer_deposits c2 JOIN proformas p1 ON p1.id = c2.proforma_id AND p1.tenant_id = $2
          WHERE c2.id = ANY({ids}) AND c2.tenant_id = $2
    UNION SELECT s1.id FROM customer_deposits c3 JOIN sales_orders s1 ON s1.quote_id = c3.quote_id AND s1.tenant_id = $2
          WHERE c3.id = ANY({ids}) AND c3.tenant_id = $2"""
SQL_CALON_SO = {
    "sales_order": "SELECT id FROM sales_orders WHERE id = $1 AND tenant_id = $2",
    "sales_invoice": "SELECT sales_order_id FROM sales_invoices WHERE id = $1 AND tenant_id = $2",
    "proforma": "SELECT sales_order_id FROM proformas WHERE id = $1 AND tenant_id = $2",
    "quote": "SELECT id FROM sales_orders WHERE quote_id = $1 AND tenant_id = $2",
    "delivery": """SELECT COALESCE(f.sales_order_id, fsi.sales_order_id) FROM invoice_fulfillments f
                   LEFT JOIN sales_invoices fsi ON fsi.id = f.invoice_id AND fsi.tenant_id = $2
                   WHERE f.id = $1 AND f.tenant_id = $2""",
    "credit_note": """SELECT nsi.sales_order_id FROM credit_notes n
                      JOIN sales_invoices nsi ON nsi.id = n.original_invoice_id AND nsi.tenant_id = $2
                      WHERE n.id = $1 AND n.tenant_id = $2""",
    "customer_deposit": _DP_SO.format(ids="ARRAY[$1]::uuid[]"),
    "receive_payment": """
        SELECT asi.sales_order_id FROM receive_payment_allocations a
        JOIN sales_invoices asi ON asi.id = a.invoice_id AND asi.tenant_id = $2
        WHERE a.payment_id = $1 AND a.tenant_id = $2
        UNION """ + _DP_SO.format(ids="COALESCE((SELECT ARRAY[r.source_deposit_id, r.created_deposit_id] FROM receive_payments r "
                                      "WHERE r.id = $1 AND r.tenant_id = $2), ARRAY[]::uuid[])"),
}


def _uuid(v: str) -> UUID:
    try:
        return UUID(str(v))
    except (ValueError, TypeError):
        raise HTTPException(status_code=404, detail="Dokumen tidak ditemukan.")


async def tentukan(conn, tenant_id: str, jenis: str, dok_id) -> dict:
    """-> {nomor, so: [verified SO ids]}; 404 bila dokumen tak ada di tabel pemilik jenisnya (tenant ini)."""
    tabel, kol = JENIS_DOKUMEN[jenis]
    nomor_row = await conn.fetchrow(f"SELECT {kol} AS nomor FROM {tabel} WHERE id = $1 AND tenant_id = $2", dok_id, tenant_id)
    if not nomor_row:
        raise HTTPException(status_code=404, detail="Dokumen tidak ditemukan.")
    calon = sorted({r[0] for r in await conn.fetch(SQL_CALON_SO[jenis], dok_id, tenant_id) if r[0] is not None}, key=str)
    so = [s for s in calon if await SL.anggota_keluarga(conn, tenant_id, s, jenis, dok_id)]
    return {"nomor": nomor_row["nomor"], "so": so}


@router.get("/{jenis}/{dok_id}")
async def lampiran_dokumen(request: Request, jenis: str, dok_id: str):
    if jenis not in JENIS_DOKUMEN:
        raise HTTPException(status_code=404, detail="Jenis dokumen tidak dikenal.")
    did = _uuid(dok_id)
    tenant_id = request.state.user["tenant_id"]
    pool = await SL.get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.tenant_id', $1, true)", tenant_id)
            t = await tentukan(conn, tenant_id, jenis, did)
            if len(t["so"]) == 1:
                so_id = t["so"][0]
                rows = await SL.baris_keluarga(conn, tenant_id, so_id)
                so_nomor = await conn.fetchval("SELECT order_number FROM sales_orders WHERE id = $1 AND tenant_id = $2",
                                               so_id, tenant_id)
                sumber = {"mode": "keluarga_so", "sales_order": {"id": str(so_id), "nomor": so_nomor}}
            else:
                rows = await SL.baris_keluarga(conn, tenant_id, None, {jenis: [did]})
                sumber = {"mode": "dokumen", "sales_order": None}
    out = await SL.susun_dengan_izin(request, rows)
    out["sumber"] = {"jenis": jenis, "id": str(did), "nomor": t["nomor"], **sumber}
    return out
