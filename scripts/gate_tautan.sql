-- GERBANG TAUTAN LEDGER -> JURNAL. Hanya BACA. Nol penyaring tenant.
--
-- [T] TERTINGGAL  baris yang SEHARUSNYA bertaut tapi tidak. "Seharusnya" =
--                 ada TEPAT SATU jurnal COGS yang cocok lewat KUNCI HALUS.
--                 Merah hari ini (>0), hijau sesudah koreksi (0).
-- [G] MENGGANTUNG tautan menunjuk jurnal yang tak ada. Lengan TETAP, bukan
--                 pemeriksaan sekali: ia MENGGANTIKAN FK yang tak terpasang
--                 (`journal_id` tak punya FK -- klaim skill v1.5 salah,
--                 diukur 12 Sep 2026). Boleh pensiun kalau FK-nya dipasang.
-- [A] AMBIGU      baris yang cocok ke LEBIH DARI SATU jurnal. Harus 0.
--                 Tautan yang ditebak lebih buruk daripada tautan yang hilang:
--                 yang hilang jujur, yang ditebak dipakai untuk membalik stok
--                 yang salah.
--
-- ⚠️ KUNCI HALUS, dan mengapa ia harus tetap halus:
--    (source_id + movement_date = journal_date + nominal kredit 1-10600)
--    Dengan `source_id` SAJA, dua baris INV-2608-0001 cocok ke DUA jurnal
--    (COGS-2607-0001 dan -0002). Keunikan 6-dari-6 adalah sifat KUNCI INI,
--    bukan sifat datanya. Menyederhanakan kuncinya = mulai menebak.

\echo '=== [T] baris yang SEHARUSNYA bertaut tapi tidak ==='
WITH cocok AS (
  SELECT il.id AS ledger_id, il.tenant_id, il.source_number, il.movement_date,
         count(je.id) AS n_jurnal
  FROM inventory_ledger il
  LEFT JOIN journal_entries je
         ON je.tenant_id = il.tenant_id
        AND je.source_type = 'INVOICE_FULFILLMENT'
        AND je.source_id::text = il.source_id::text
        AND je.journal_date = il.movement_date
        AND (SELECT COALESCE(SUM(jl.credit),0) FROM journal_lines jl
               JOIN chart_of_accounts coa ON coa.id = jl.account_id
              WHERE jl.journal_id = je.id AND coa.account_code = '1-10600')
            = round(il.quantity_out * il.unit_cost, 2)
  WHERE il.journal_id IS NULL AND il.source_type = 'INVOICE_FULFILLMENT'
  GROUP BY 1,2,3,4)
SELECT tenant_id, source_number, movement_date, n_jurnal
FROM cocok WHERE n_jurnal = 1 ORDER BY tenant_id, movement_date;

\echo ''
\echo '=== PUTUSAN ==='
WITH cocok AS (
  SELECT il.id, count(je.id) AS n
  FROM inventory_ledger il
  LEFT JOIN journal_entries je
         ON je.tenant_id = il.tenant_id
        AND je.source_type = 'INVOICE_FULFILLMENT'
        AND je.source_id::text = il.source_id::text
        AND je.journal_date = il.movement_date
        AND (SELECT COALESCE(SUM(jl.credit),0) FROM journal_lines jl
               JOIN chart_of_accounts coa ON coa.id = jl.account_id
              WHERE jl.journal_id = je.id AND coa.account_code = '1-10600')
            = round(il.quantity_out * il.unit_cost, 2)
  WHERE il.journal_id IS NULL AND il.source_type = 'INVOICE_FULFILLMENT'
  GROUP BY 1)
SELECT
  (SELECT count(*) FROM cocok WHERE n = 1) AS "T_tertinggal",
  (SELECT count(*) FROM inventory_ledger il WHERE il.journal_id IS NOT NULL
     AND NOT EXISTS (SELECT 1 FROM journal_entries je WHERE je.id = il.journal_id))
     AS "G_menggantung",
  (SELECT count(*) FROM cocok WHERE n > 1) AS "A_ambigu",
  (SELECT count(*) FROM inventory_ledger WHERE journal_id IS NOT NULL)
     AS "total_bertaut_utk_banding";
\echo '(T=0 G=0 A=0 -> LULUS. T>0 -> ada yang tertinggal. A>0 -> BERHENTI, jangan menebak.)'
