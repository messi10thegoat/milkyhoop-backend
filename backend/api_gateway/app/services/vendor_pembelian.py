"""Pembelian per vendor TURUNAN JURNAL (B2, 4 Nov 2026) -- cermin services/pelanggan_penjualan (simetri Pelanggan <-> Vendor).
Definisi (Law 1/16): total_pembelian = Σ kredit HUTANG jurnal BILL (POSTED, tak dibalik) - Σ debit HUTANG jurnal VENDOR_CREDIT (POSTED,
tak dibalik); tagihan saldo awal (is_opening_balance) BUKAN pembelian. jumlah_tagihan = tagihan non-draf non-void; tanggal terakhirnya.
Satu kueri untuk satu halaman, berpagar tenant."""
from decimal import Decimal

SQL_PEMBELIAN = """
    WITH f AS (
        SELECT b.vendor_id AS vid, COUNT(*) AS n, MAX(b.issue_date) AS terakhir
        FROM bills b
        WHERE b.tenant_id = $1 AND b.vendor_id = ANY($2::uuid[]) AND COALESCE(b.status_v2, b.status) NOT IN ('draft', 'void')
          AND NOT COALESCE(b.is_opening_balance, false)
        GROUP BY 1
    ), ap AS (
        SELECT b.vendor_id AS vid, SUM(jl.credit) AS kredit
        FROM bills b
        JOIN journal_entries je ON je.tenant_id = b.tenant_id AND je.source_type = 'BILL'
             AND je.source_id::text = b.id::text AND je.status = 'POSTED' AND je.reversed_by_id IS NULL
        JOIN journal_lines jl ON jl.journal_id = je.id
        JOIN chart_of_accounts coa ON coa.id = jl.account_id AND coa.account_type = 'PAYABLE'
        WHERE b.tenant_id = $1 AND b.vendor_id = ANY($2::uuid[]) AND NOT COALESCE(b.is_opening_balance, false)
        GROUP BY 1
    ), vc AS (
        SELECT v.vendor_id AS vid, SUM(jl.debit) AS debit
        FROM vendor_credits v
        JOIN journal_entries je ON je.tenant_id = v.tenant_id AND je.source_type = 'VENDOR_CREDIT'
             AND je.source_id::text = v.id::text AND je.status = 'POSTED' AND je.reversed_by_id IS NULL
        JOIN journal_lines jl ON jl.journal_id = je.id
        JOIN chart_of_accounts coa ON coa.id = jl.account_id AND coa.account_type = 'PAYABLE'
        WHERE v.tenant_id = $1 AND v.vendor_id = ANY($2::uuid[])
        GROUP BY 1
    )
    SELECT k.vid, COALESCE(f.n, 0) AS n, f.terakhir, COALESCE(ap.kredit, 0) - COALESCE(vc.debit, 0) AS total
    FROM (SELECT vid FROM f UNION SELECT vid FROM ap UNION SELECT vid FROM vc) k
    LEFT JOIN f ON f.vid = k.vid LEFT JOIN ap ON ap.vid = k.vid LEFT JOIN vc ON vc.vid = k.vid
"""

KOSONG = {"total_pembelian": 0, "jumlah_tagihan": 0, "last_bill_date": None}


async def pembelian_vendor(conn, tenant_id: str, vendor_ids) -> dict:
    from ..schemas.uang_json import _uang_json
    ids = [str(i) for i in vendor_ids]
    hasil = {i: dict(KOSONG) for i in ids}
    if not ids:
        return hasil
    for r in await conn.fetch(SQL_PEMBELIAN, tenant_id, ids):
        k = str(r["vid"])
        if k in hasil:
            hasil[k] = {"total_pembelian": _uang_json(Decimal(str(r["total"] or 0))), "jumlah_tagihan": int(r["n"] or 0),
                        "last_bill_date": r["terakhir"].isoformat() if r["terakhir"] else None}
    return hasil
