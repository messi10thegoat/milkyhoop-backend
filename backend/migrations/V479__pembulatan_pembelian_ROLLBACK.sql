-- ROLLBACK V479. Menolak bila sudah ada dokumen ber-pembulatan <> 0 atau baris jurnal di akun 5-90100 (fakta buku). Hapus HANYA yang
-- ditulis V479 (peran notes 'V479%', akun 5-90100 'Pembulatan Pembelian', baris semai, kolom pembulatan, kolom beku). CHECK role_key
-- dibiarkan (nilai tambahan tak berbahaya; pola ROLLBACK V417/V452/V471).
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM bills WHERE pembulatan <> 0) OR EXISTS (SELECT 1 FROM vendor_credits WHERE pembulatan <> 0)
       OR EXISTS (SELECT 1 FROM journal_lines jl JOIN chart_of_accounts c ON c.id = jl.account_id
                  WHERE c.account_code = '5-90100' AND c.name = 'Pembulatan Pembelian') THEN
        RAISE EXCEPTION 'ROLLBACK V479 ditolak: sudah ada dokumen berpembulatan / baris jurnal di 5-90100';
    END IF;
END $$;

DELETE FROM account_roles WHERE role_key = 'PURCHASE_ROUNDING' AND notes LIKE 'V479%';
DELETE FROM chart_of_accounts c WHERE c.account_code = '5-90100' AND c.name = 'Pembulatan Pembelian'
  AND NOT EXISTS (SELECT 1 FROM account_roles a WHERE a.account_id = c.id);

DO $$
DECLARE v_def text; v_baru text;
BEGIN
    SELECT pg_get_triggerdef(t.oid) INTO v_def FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid
    WHERE c.relname = 'bills' AND t.tgname = 'trg_law19_bekukan_nominal';
    IF v_def LIKE '%''pembulatan''%' THEN
        EXECUTE 'DROP TRIGGER trg_law19_bekukan_nominal ON bills';
        EXECUTE replace(v_def, ', ''pembulatan''', '');
    END IF;
    v_def := pg_get_functiondef('seed_default_coa(text)'::regprocedure);
    IF v_def LIKE '%''5-90100'', ''Pembulatan Pembelian''%' THEN
        v_baru := replace(v_def, E'(p_tenant_id, ''5-90100'', ''Pembulatan Pembelian'', ''OTHER_EXPENSE'', ''DEBIT'', ''5-90000'', 2, false, false),\n'
                          '        ', '');
        IF v_baru = v_def THEN RAISE EXCEPTION 'ROLLBACK V479: baris 5-90100 tak ditemukan di seed_default_coa'; END IF;
        EXECUTE replace(v_baru, 'v_count := v_count + 27;', 'v_count := v_count + 26;');
    END IF;
    v_def := pg_get_functiondef('seed_default_account_roles(text)'::regprocedure);
    IF v_def LIKE '%V479 Pembulatan%' THEN
        v_baru := replace(v_def, E'\n'
                          '            (''PURCHASE_ROUNDING'',          ''5-90100'', false, ''V479 Pembulatan total dokumen pembelian ke rupiah penuh.''),', '');
        IF v_baru = v_def THEN RAISE EXCEPTION 'ROLLBACK V479: baris PURCHASE_ROUNDING tak ditemukan di seed_default_account_roles'; END IF;
        EXECUTE v_baru;
    END IF;
END $$;

ALTER TABLE bills DROP COLUMN IF EXISTS pembulatan;
ALTER TABLE vendor_credits DROP COLUMN IF EXISTS pembulatan;
