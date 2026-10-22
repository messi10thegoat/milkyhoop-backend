-- ROLLBACK V439. Menolak bila sudah ada dokumen GRN atau jurnal GOODS_RECEIPT* atau baris jurnal di akun baru (fakta buku).
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM goods_receipts)
       OR EXISTS (SELECT 1 FROM journal_entries WHERE source_type IN ('GOODS_RECEIPT', 'GOODS_RECEIPT_REVERSAL', 'GOODS_RECEIPT_RETURN'))
       OR EXISTS (SELECT 1 FROM journal_lines jl JOIN chart_of_accounts c ON c.id = jl.account_id
                  WHERE c.account_code IN ('2-10150', '5-10400')) THEN
        RAISE EXCEPTION 'ROLLBACK V439 ditolak: sudah ada GRN / jurnal GOODS_RECEIPT* / baris jurnal di 2-10150 atau 5-10400';
    END IF;
END $$;
DROP TABLE goods_receipt_bill_links;
DROP TABLE goods_receipt_items;
DROP TABLE goods_receipts;
DELETE FROM account_roles WHERE role_key IN ('GOODS_RECEIVED_NOT_INVOICED', 'PURCHASE_PRICE_VARIANCE');
DELETE FROM chart_of_accounts WHERE (account_code = '2-10150' AND name = 'Barang Diterima Belum Ditagih')
                                 OR (account_code = '5-10400' AND name = 'Selisih Harga Pembelian');
DELETE FROM journal_source_types WHERE source_type IN ('GOODS_RECEIPT', 'GOODS_RECEIPT_REVERSAL', 'GOODS_RECEIPT_RETURN');
-- CHECK role_key, seed_default_*, is_effective_journal: nilai tambahan tak berbahaya dibiarkan (dilonggarkan saja; pola ROLLBACK V417).
