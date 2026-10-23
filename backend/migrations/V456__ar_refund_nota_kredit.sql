-- V456 (BACKEND2 23 Okt 2026; arah DISETUJUI LANGSUNG pemilik "Ya, bangun perbaikannya"; terapan = izin terpisah) -- audit F2 dashboard T2.
-- compute_ar_outstanding: baris kredit nota kredit TAK TERTAUT (cn_unapplied) kini dikurangi REFUND-nya (jurnal CREDIT_NOTE_REFUND, Dr Piutang,
-- source_id = nota kredit). Kembar sisi AP yang sudah ada sejak V441 (VENDOR_CREDIT_REFUND di vc_unapplied). Tanpa ini: kredit pelanggan tetap
-- tampil sesudah dikembalikan tunai -> subledger < GL (kaos RF-2610-0001, Δ 1.000). Nol jurnal; hanya rumus baca.
-- Ditambal dari badan SEKARANG (pg_get_functiondef + anchor gagal-keras), bukan CREATE OR REPLACE badan lama (pelajaran V452).
-- Jalankan -v ON_ERROR_STOP=1 -1.
DO $$
DECLARE v_def text; v_baru text;
BEGIN
    v_def := pg_get_functiondef('compute_ar_outstanding(text)'::regprocedure);
    IF v_def LIKE '%CREDIT_NOTE_REFUND%' THEN
        RAISE NOTICE 'V456: sudah terpasang, lewati';
        RETURN;
    END IF;
    v_baru := replace(v_def,
        E'          AND je.source_type = ''CREDIT_NOTE''\n          AND coa.account_type = ''RECEIVABLE''\n          AND cn.original_invoice_id IS NULL',
        E'          AND je.source_type IN (''CREDIT_NOTE'', ''CREDIT_NOTE_REFUND'')  -- V456: refund (Dr Piutang) mengurangi kredit tak tertaut\n          AND coa.account_type = ''RECEIVABLE''\n          AND cn.original_invoice_id IS NULL');
    IF v_baru = v_def THEN
        RAISE EXCEPTION 'V456: anchor cn_unapplied (source_type CREDIT_NOTE + original_invoice_id IS NULL) tak ditemukan';
    END IF;
    IF (length(v_baru) - length(replace(v_baru, 'CREDIT_NOTE_REFUND', ''))) / length('CREDIT_NOTE_REFUND') <> 1 THEN
        RAISE EXCEPTION 'V456: anchor tidak tunggal';
    END IF;
    EXECUTE v_baru;
    IF pg_get_functiondef('compute_ar_outstanding(text)'::regprocedure) NOT LIKE '%CREDIT_NOTE_REFUND%' THEN
        RAISE EXCEPTION 'V456: tambalan tak terpasang';
    END IF;
END $$;
