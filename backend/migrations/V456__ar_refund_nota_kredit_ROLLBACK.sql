-- ROLLBACK V456: kembalikan cn_unapplied ke source_type = 'CREDIT_NOTE' saja (kebalikan tambalan persis). Nol jurnal.
DO $$
DECLARE v_def text; v_baru text;
BEGIN
    v_def := pg_get_functiondef('compute_ar_outstanding(text)'::regprocedure);
    v_baru := replace(v_def,
        E'          AND je.source_type IN (''CREDIT_NOTE'', ''CREDIT_NOTE_REFUND'')  -- V456: refund (Dr Piutang) mengurangi kredit tak tertaut\n',
        E'          AND je.source_type = ''CREDIT_NOTE''\n');
    IF v_baru = v_def THEN RAISE EXCEPTION 'ROLLBACK V456: tambalan tak ditemukan'; END IF;
    EXECUTE v_baru;
END $$;
