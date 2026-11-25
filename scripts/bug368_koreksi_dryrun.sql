-- BUG-368 kaos-biru-konveksi: DRY-RUN. Nol tulis (transaksi READ ONLY). Hanya menampilkan jurnal yang AKAN dibuat.
-- Eksekusi nyata BUKAN lewat skrip ini: pakai jalur aplikasi (penulis OPENING / reversal resmi) setelah izin pemilik.
-- Jalankan: psql -U postgres -d milkydb -f scripts/bug368_koreksi_dryrun.sql
BEGIN READ ONLY;
\pset pager off
\echo == (1) jurnal OPENING yang akan dibuat untuk ledger OB tanpa jurnal
SELECT il.source_number, il.movement_date AS tanggal_jurnal, il.product_name,
       '1-10600 Persediaan' AS debit_akun, (il.quantity_in*il.unit_cost) AS debit,
       'Ekuitas saldo awal (akun OPENING tenant)' AS kredit_akun, (il.quantity_in*il.unit_cost) AS kredit
FROM inventory_ledger il
WHERE il.tenant_id='kaos-biru-konveksi' AND il.source_type='OPENING_BALANCE' AND il.journal_id IS NULL;
\echo == (2) reversal yang akan dibuat untuk jurnal BILL tanpa ledger (balik Dr/Cr, reversed_by_id)
SELECT je.journal_number, je.journal_date, je.description,
       jl.account_id, c.account_code, jl.credit AS reversal_debit, jl.debit AS reversal_credit
FROM journal_entries je
JOIN journal_lines jl ON jl.journal_id=je.id
JOIN chart_of_accounts c ON c.id=jl.account_id
WHERE je.tenant_id='kaos-biru-konveksi' AND je.source_type='BILL' AND je.status='POSTED' AND je.reversed_by_id IS NULL
  AND je.journal_number IN ('PJ-2609-0008','PJ-2609-0009','PJ-2609-0010','PJ-2609-0021')
  AND NOT EXISTS (SELECT 1 FROM inventory_ledger il WHERE il.journal_id=je.id)
ORDER BY je.journal_number, c.account_code;
\echo == hasil yang diharapkan: selisih ledger-GL 47.994,16 -> 0,16 (sisa = pembulatan GRN-2611-0008)
COMMIT;
