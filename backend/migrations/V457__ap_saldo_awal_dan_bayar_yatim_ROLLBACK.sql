-- ROLLBACK V457: buang CTE ob_rows/bayar_yatim/yatim_rows dan dua UNION-nya (kembali ke bill_rows UNION vc_rows). Nol jurnal.
DO $$
DECLARE v_def text; v_baru text; i int; j int;
BEGIN
    v_def := pg_get_functiondef('compute_ap_outstanding(text)'::regprocedure);
    i := position(E'    , -- V457 (1)' IN v_def);
    j := position(E'    SELECT * FROM bill_rows WHERE outstanding != 0' IN v_def);
    IF i = 0 OR j = 0 OR j < i THEN RAISE EXCEPTION 'ROLLBACK V457: tambalan tak ditemukan'; END IF;
    v_baru := substr(v_def, 1, i - 1) || substr(v_def, j);
    v_baru := replace(v_baru, E'    UNION ALL\n    SELECT * FROM ob_rows WHERE outstanding != 0\n', '');
    v_baru := replace(v_baru, E'    UNION ALL\n    SELECT * FROM yatim_rows WHERE outstanding != 0\n', '');
    IF v_baru LIKE '%OPENING-BALANCE%' OR v_baru LIKE '%yatim_rows%' THEN RAISE EXCEPTION 'ROLLBACK V457: sisa tambalan'; END IF;
    EXECUTE v_baru;
END $$;
