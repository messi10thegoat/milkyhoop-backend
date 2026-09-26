"""Penjualan per pelanggan TURUNAN JURNAL (26 Sep 2026) — pengganti cache mati customers.total_nilai/total_transaksi.

Terukur: kolom cache 0 di SEMUA tenant (penulisnya hanya /api/members add-points, tak pernah dipanggil) sehingga
GET /api/customers mengirim total_value/total_transactions = 0 padahal grapgrap punya 27 faktur Rp 153,59 jt.
Definisi (Law 1/16, sumber = jurnal):
- total_penjualan = Σ debit PIUTANG jurnal INVOICE (POSTED, tak dibalik) − Σ kredit PIUTANG jurnal CREDIT_NOTE
  (POSTED, tak dibalik) — bruto termasuk PPN, bersih retur/nota kredit; faktur void otomatis keluar (jurnal dibalik).
- jumlah_faktur = faktur non-draf non-void; last_invoice_date = invoice_date terbarunya.
Satu kueri untuk seluruh halaman, berpagar tenant.
"""
from decimal import Decimal

SQL_PENJUALAN = """
    WITH f AS (
        SELECT si.customer_id AS cid, COUNT(*) AS n, MAX(si.invoice_date) AS terakhir
        FROM sales_invoices si
        WHERE si.tenant_id = $1 AND si.customer_id = ANY($2::uuid[]) AND si.status NOT IN ('draft', 'void')
        GROUP BY 1
    ), ar AS (
        SELECT si.customer_id AS cid, SUM(jl.debit) AS debit
        FROM sales_invoices si
        JOIN journal_entries je ON je.tenant_id = si.tenant_id AND je.source_type = 'INVOICE'
             AND je.source_id::text = si.id::text AND je.status = 'POSTED' AND je.reversed_by_id IS NULL
        JOIN journal_lines jl ON jl.journal_id = je.id
        JOIN chart_of_accounts coa ON coa.id = jl.account_id AND coa.account_type = 'RECEIVABLE'
        WHERE si.tenant_id = $1 AND si.customer_id = ANY($2::uuid[])
        GROUP BY 1
    ), cn AS (
        SELECT c.customer_id AS cid, SUM(jl.credit) AS kredit
        FROM credit_notes c
        JOIN journal_entries je ON je.tenant_id = c.tenant_id AND je.source_type = 'CREDIT_NOTE'
             AND je.source_id::text = c.id::text AND je.status = 'POSTED' AND je.reversed_by_id IS NULL
        JOIN journal_lines jl ON jl.journal_id = je.id
        JOIN chart_of_accounts coa ON coa.id = jl.account_id AND coa.account_type = 'RECEIVABLE'
        WHERE c.tenant_id = $1 AND c.customer_id = ANY($2::uuid[])
        GROUP BY 1
    )
    SELECT k.cid, COALESCE(f.n, 0) AS n, f.terakhir,
           COALESCE(ar.debit, 0) - COALESCE(cn.kredit, 0) AS total
    FROM (SELECT cid FROM f UNION SELECT cid FROM ar UNION SELECT cid FROM cn) k
    LEFT JOIN f ON f.cid = k.cid LEFT JOIN ar ON ar.cid = k.cid LEFT JOIN cn ON cn.cid = k.cid
"""

KOSONG = {"total_penjualan": 0.0, "jumlah_faktur": 0, "last_invoice_date": None}


async def penjualan_pelanggan(conn, tenant_id: str, customer_ids) -> dict:
    ids = [str(i) for i in customer_ids]
    hasil = {i: dict(KOSONG) for i in ids}
    if not ids:
        return hasil
    for r in await conn.fetch(SQL_PENJUALAN, tenant_id, ids):
        k = str(r["cid"])
        if k in hasil:
            hasil[k] = {"total_penjualan": float(Decimal(str(r["total"] or 0))), "jumlah_faktur": int(r["n"] or 0),
                        "last_invoice_date": r["terakhir"].isoformat() if r["terakhir"] else None}
    return hasil
