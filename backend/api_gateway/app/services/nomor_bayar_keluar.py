"""Nomor Pembayaran Keluar -- SATU seri dokumen BP-YYMM-NNNN untuk kedua rute (putusan MASTER 4 Nov 2026, syarat U-A).

Dulu dua generator: rute lama /bills/{id}/payments menerbitkan BP- dari penghitung JURNAL berawalan BP, rute baru /bill-payments
menerbitkan PAY- dari bill_payment_sequences. Kini SATU penentu PENGHITUNG DOKUMEN (penjaga test_nomor_jurnal_satu_ruang: seri jurnal
!= seri dokumen): nomor berikutnya = BP- tertinggi bulan bisnis ini yang SUDAH dipakai (bill_payments_v2 ATAU nomor jurnal) tenant + 1, di bawah kunci
advisory per tenant (pola V475: melompati yang terpakai, termasuk nomor manual/lama). Nol tabel/migrasi baru; UNIQUE (tenant_id,
payment_number) V088 tetap pagar terakhir. Nomor JURNAL pembayaran = nomor_jurnal_dokumen (standar dokumen lain, cadangan PKJ).
Nomor PAY- lama dibiarkan; bill_payment_sequences tak ditulis lagi.
"""
SQL_NOMOR_PK = """
WITH k AS (SELECT to_char(tanggal_bisnis($1), 'YYMM') AS yymm),
     terpakai AS (
       SELECT bp.payment_number AS nomor FROM bill_payments_v2 bp, k
        WHERE bp.tenant_id = $1 AND bp.payment_number ~ ('^BP-' || k.yymm || '-[0-9]+$')
       UNION ALL  -- jurnal BP- seri jurnal lama tanpa dokumen (kaos 4 Nov: 4 baris) -> nomor dokumen tak pernah memakai nomor jurnal lain
       SELECT je.journal_number FROM journal_entries je, k
        WHERE je.tenant_id = $1 AND je.journal_number ~ ('^BP-' || k.yymm || '-[0-9]+$'))
SELECT 'BP-' || k.yymm || '-' || lpad((COALESCE(MAX((regexp_match(t.nomor, '^BP-' || k.yymm || '-([0-9]+)$'))[1]::int), 0) + 1)::text,
                                      4, '0')
FROM k LEFT JOIN terpakai t ON true
GROUP BY k.yymm
"""


async def nomor_pk(conn, tid: str) -> str:
    """Penulis (di transaksi pemanggil): kunci advisory per tenant lalu nomor berikutnya -- dua penulis bersamaan tak bentrok."""
    await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", f"NOMOR_PK:{tid}")
    return await conn.fetchval(SQL_NOMOR_PK, tid)


async def perkiraan_nomor_pk(conn, tid: str) -> str:
    """Pratinjau (baca saja, tanpa kunci): rumus YANG SAMA; perkiraan -- penulis paralel bisa mendahului."""
    return await conn.fetchval(SQL_NOMOR_PK, tid)
