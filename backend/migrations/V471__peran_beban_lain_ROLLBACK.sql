-- ROLLBACK V471: hapus HANYA pemetaan yang ditulis V471 (role EXPENSE_OTHER + notes 'V471%') dan baris semainya (anchor balik).
-- Pemetaan EXPENSE_OTHER yang diubah pengguna sesudahnya (notes bukan V471) tak disentuh. CHECK role_key dibiarkan (nilai tambahan
-- tak berbahaya; pola ROLLBACK V417/V452). Nol jurnal / akun disentuh.
DELETE FROM account_roles WHERE role_key = 'EXPENSE_OTHER' AND notes LIKE 'V471%';

DO $$
DECLARE v_def text; v_baru text;
BEGIN
    v_def := pg_get_functiondef('seed_default_account_roles(text)'::regprocedure);
    IF v_def LIKE '%V471 Beban lain-lain%' THEN
        v_baru := replace(v_def, E'\n'
                          '            (''EXPENSE_OTHER'',              ''5-20900'', false, ''V471 Beban lain-lain bawaan catat biaya.''),', '');
        IF v_baru = v_def THEN RAISE EXCEPTION 'ROLLBACK V471: baris EXPENSE_OTHER tak ditemukan di seed_default_account_roles'; END IF;
        EXECUTE v_baru;
    END IF;
END $$;
