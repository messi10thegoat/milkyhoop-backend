"""Lampiran keluarga untuk dokumen penjualan APA PUN, juga TANPA SO (LAMPIRAN-TANPA-SO, 15 Okt 2026, putusan MASTER).

GET /api/lampiran/{jenis}/{id}   jenis = sales_order | sales_invoice | receive_payment | customer_deposit | proforma | delivery | credit_note
                                        | quote (LAMPIRAN-NK-QUOTE 15 Okt)
                                        | bill | payment_out (LAMPIRAN-PEMBELIAN F1, 30 Okt 2026: keluarga FPB <-> PK, lihat JENIS_PEMBELIAN)
  -> bentuk SAMA dengan GET /api/sales-orders/{id}/lampiran ({success, data[grup], jumlah}) + `sumber`:
     {"jenis", "id", "nomor", "mode": "keluarga_so" | "dokumen" | "keluarga_pembelian", "sales_order": {"id", "nomor"} | null}

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


# LAMPIRAN-PEMBELIAN F1 (30 Okt 2026, putusan MASTER (ii)): jenis Pembelian -> (tabel pemilik, kolom nomor). Keluarga SATU langkah, cermin
# faktur <-> penerimaan Penjualan: FPB -> PK yang mengalokasikannya (bill_payment_allocations); PK -> FPB yang dialokasikannya.
# entity_type 'payment' dipakai BERSAMA penerimaan: grup payment_out HANYA id yang ADA di bill_payments_v2 tenant ini (JOIN pemilik),
# jadi lampiran milik receive_payments tak pernah ikut (dan sebaliknya: receive_payment hanya receive_payments).
# FPB punya DUA sumber (== GET /api/bills/{id}/attachments): bill_attachments (unggah lama, MinIO -> 's3'; tanpa tenant_id -> pagar lewat
# JOIN bills) + document_attachments entity_type 'bill'. Tenant eksplisit di setiap tabel (gateway BYPASSRLS).
JENIS_PEMBELIAN = {
    "bill": ("bills", "invoice_number"),
    "payment_out": ("bill_payments_v2", "payment_number"),
}
SQL_KELUARGA_BELI = """
WITH fb0 AS (
    SELECT b0x.id FROM bills b0x WHERE b0x.tenant_id = $2 AND b0x.id = $1 AND $3 = 'bill'
), pk0 AS (
    SELECT p0x.id FROM bill_payments_v2 p0x WHERE p0x.tenant_id = $2 AND p0x.id = $1 AND $3 = 'payment_out'
), fb AS (
    SELECT bx.id FROM bills bx
    WHERE bx.tenant_id = $2
      AND (bx.id IN (SELECT id FROM fb0)
           OR bx.id IN (SELECT a0x.bill_id FROM bill_payment_allocations a0x WHERE a0x.payment_id IN (SELECT id FROM pk0)))
), pk AS (
    SELECT px.id FROM bill_payments_v2 px
    WHERE px.tenant_id = $2
      AND (px.id IN (SELECT id FROM pk0)
           OR px.id IN (SELECT a1x.payment_id FROM bill_payment_allocations a1x WHERE a1x.bill_id IN (SELECT id FROM fb0)))
), meta AS (
    SELECT 'bill'::text AS jenis, 'bill'::text AS et, b.id, b.invoice_number::text AS nomor, b.issue_date::date AS tanggal,
           COALESCE(b.status_v2, b.status)::text AS status
    FROM bills b WHERE b.tenant_id = $2 AND b.id IN (SELECT id FROM fb)
    UNION ALL
    SELECT 'payment_out', 'payment', p.id, p.payment_number, p.payment_date::date, p.status
    FROM bill_payments_v2 p WHERE p.tenant_id = $2 AND p.id IN (SELECT id FROM pk)
)
SELECT m.jenis, m.id AS dok_id, m.nomor, m.tanggal, m.status,
       d.id AS lampiran_id, COALESCE(da.display_name, d.file_name) AS nama,
       d.file_type AS mime, d.file_size AS ukuran, d.storage_type, d.uploaded_at,
       COALESCE(NULLIF(trim(up.display_name), ''), NULLIF(trim(u.fullname), ''),
                NULLIF(trim(u.name), ''), u.email) AS pengunggah,
       'hub'::text AS sumber
FROM meta m
JOIN document_attachments da ON da.entity_type = m.et AND da.entity_id = m.id AND da.tenant_id = $2
JOIN documents d ON d.id = da.document_id AND d.tenant_id = $2 AND d.deleted_at IS NULL
LEFT JOIN "User" u ON u.id = d.uploaded_by::text
LEFT JOIN user_profiles up ON up.user_id = u.id
UNION ALL
SELECT m.jenis, m.id, m.nomor, m.tanggal, m.status,
       ba.id, ba.filename, ba.mime_type, ba.file_size, 's3', ba.uploaded_at,
       COALESCE(NULLIF(trim(up.display_name), ''), NULLIF(trim(u.fullname), ''),
                NULLIF(trim(u.name), ''), u.email),
       'tagihan_lama'
FROM meta m
JOIN bills bb ON bb.id = m.id AND bb.tenant_id = $2
JOIN bill_attachments ba ON ba.bill_id = bb.id
LEFT JOIN "User" u ON u.id = ba.uploaded_by::text
LEFT JOIN user_profiles up ON up.user_id = u.id
WHERE m.jenis = 'bill'
"""


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
    if jenis not in JENIS_DOKUMEN and jenis not in JENIS_PEMBELIAN:
        raise HTTPException(status_code=404, detail="Jenis dokumen tidak dikenal.")
    did = _uuid(dok_id)
    tenant_id = request.state.user["tenant_id"]
    pool = await SL.get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.tenant_id', $1, true)", tenant_id)
            if jenis in JENIS_PEMBELIAN:
                tabel, kol = JENIS_PEMBELIAN[jenis]
                nomor = await conn.fetchrow(f"SELECT {kol} AS nomor FROM {tabel} WHERE id = $1 AND tenant_id = $2", did, tenant_id)
                if not nomor:
                    raise HTTPException(status_code=404, detail="Dokumen tidak ditemukan.")
                rows = await conn.fetch(SQL_KELUARGA_BELI, did, tenant_id, jenis)
                t = {"nomor": nomor["nomor"]}
                sumber = {"mode": "keluarga_pembelian", "sales_order": None}
            else:
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
