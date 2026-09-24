-- Gerbang V303 (#10b-3a): 4 fungsi inti AR/AP tak lagi memakai CURRENT_DATE (UTC),
-- dan tetap bisa dieksekusi. Dijalankan di DB scratch (lihat gate_t10b3a_v303.sh).
-- Tanpa migrasi (definisi prod) = KONTROL MERAH -> harus GAGAL.
\set ON_ERROR_STOP 1
DO $$
DECLARE
    f text;
    src text;
    t text;
    salah int := 0;
BEGIN
    FOREACH f IN ARRAY ARRAY['compute_ar_outstanding','compute_ap_outstanding','compute_ar_summary','compute_ap_summary'] LOOP
        SELECT prosrc INTO src FROM pg_proc WHERE proname = f;
        IF src ~* 'current_date' THEN
            RAISE WARNING 'GAGAL: % masih memakai CURRENT_DATE', f; salah := salah + 1;
        END IF;
        IF src !~ 'tanggal_bisnis\(p_tenant_id\)' THEN
            RAISE WARNING 'GAGAL: % tidak memakai tanggal_bisnis(p_tenant_id)', f; salah := salah + 1;
        END IF;
    END LOOP;
    -- Eksekusi nyata untuk setiap tenant (scratch tanpa jurnal -> 0 baris, tapi SQL-nya dijalankan).
    FOR t IN SELECT id FROM "Tenant" LOOP
        PERFORM * FROM compute_ar_summary(t);
        PERFORM * FROM compute_ap_summary(t);
    END LOOP;
    IF salah > 0 THEN
        RAISE EXCEPTION 'GAGAL: % pelanggaran', salah;
    END IF;
    RAISE NOTICE 'LULUS: V303 4 fungsi bertanggal bisnis, tereksekusi untuk semua tenant';
END $$;
