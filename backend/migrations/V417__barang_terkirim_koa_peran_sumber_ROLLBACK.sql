-- Rollback V417. -v ON_ERROR_STOP=1 -1. Menolak bila ada jurnal DELIVERY_* atau alokasi bernilai (fakta buku).
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM journal_entries WHERE source_type IN ('DELIVERY_NOTE','DELIVERY_NOTE_REVERSAL','DELIVERY_INVOICED','DELIVERY_INVOICED_REVERSAL'))
       OR EXISTS (SELECT 1 FROM sj_faktur_alokasi WHERE total_cost <> 0) THEN
        RAISE EXCEPTION 'ROLLBACK V417 ditolak: ada jurnal DELIVERY_* atau alokasi bernilai';
    END IF;
END $$;
DELETE FROM account_roles WHERE role_key = 'GOODS_DELIVERED_NOT_INVOICED';
DELETE FROM chart_of_accounts WHERE account_code = '1-10660' AND name = 'Barang Terkirim'
  AND NOT EXISTS (SELECT 1 FROM journal_lines jl WHERE jl.account_id = chart_of_accounts.id);
DELETE FROM journal_source_types WHERE source_type IN ('DELIVERY_NOTE','DELIVERY_NOTE_REVERSAL','DELIVERY_INVOICED','DELIVERY_INVOICED_REVERSAL');
ALTER TABLE sj_faktur_alokasi DROP COLUMN IF EXISTS revenue_journal_id;
ALTER TABLE sj_faktur_alokasi DROP COLUMN IF EXISTS cogs_journal_id;
ALTER TABLE sj_faktur_alokasi DROP COLUMN IF EXISTS total_cost;
-- CHECK role_key, seed_default_*, is_effective_journal: nilai tambahan tak berbahaya dibiarkan (dilonggarkan saja).
