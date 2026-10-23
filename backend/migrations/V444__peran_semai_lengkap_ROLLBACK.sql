-- ROLLBACK V444: hapus HANYA yang ditulis V444 (penanda notes/description 'V444'); akun 1-10450 buatan V444 yang SUDAH terpakai jurnal
-- -> gagal-keras (jangan hapus akun terpakai); kembalikan dua fungsi semai lewat anchor balik. grapgrap/kaos 1-10450 lama (V281) tak tersentuh.
DO $$
DECLARE n int;
BEGIN
    SELECT count(*) INTO n FROM journal_lines jl JOIN chart_of_accounts c ON c.id = jl.account_id
    WHERE c.account_code = '1-10450' AND c.description LIKE 'V444%';
    IF n > 0 THEN RAISE EXCEPTION 'ROLLBACK V444: 1-10450 buatan V444 sudah dipakai % baris jurnal -- tak dihapus', n; END IF;
END $$;

DELETE FROM account_roles WHERE notes LIKE 'V444%';
DELETE FROM chart_of_accounts WHERE account_code = '1-10450' AND description LIKE 'V444%';

DO $$
DECLARE v_def text; v_baru text;
BEGIN
    v_def := pg_get_functiondef('seed_default_coa(text)'::regprocedure);
    IF v_def LIKE '%1-10450%' THEN
        v_baru := replace(v_def, E'\n        (p_tenant_id, ''1-10450'', ''Piutang Karyawan (Kasbon)'', ''ASSET'', ''DEBIT'', ''1-10000'', 2, false),', '');
        IF v_baru = v_def THEN RAISE EXCEPTION 'ROLLBACK V444: baris 1-10450 tak ditemukan di seed_default_coa'; END IF;
        v_baru := replace(v_baru, 'v_count := v_count + 20;', 'v_count := v_count + 19;');
        EXECUTE v_baru;
    END IF;
    v_def := pg_get_functiondef('seed_default_account_roles(text)'::regprocedure);
    IF v_def LIKE '%V444%' THEN
        v_baru := replace(v_def, E'\n'
                          '            (''EMPLOYEE_ADVANCE'',           ''1-10450'', false, ''V444 Piutang Karyawan (Kasbon).''),\n'
                          '            (''BANK_ADJUSTMENT_GAIN'',       ''4-90000'', false, ''V444 Selisih lebih penyesuaian saldo bank.''),\n'
                          '            (''BANK_ADJUSTMENT_LOSS'',       ''5-20900'', false, ''V444 Selisih kurang penyesuaian saldo bank.''),\n'
                          '            (''REVENUE_SALES_SERVICE'',      ''4-10150'', false, ''V444 Pendapatan Jasa.''),', '');
        IF v_baru = v_def THEN RAISE EXCEPTION 'ROLLBACK V444: baris V444 tak ditemukan di seed_default_account_roles'; END IF;
        EXECUTE v_baru;
    END IF;
END $$;
