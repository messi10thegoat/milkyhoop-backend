-- Gerbang V306 (backlog 3b): 15 fungsi DB bertanggal BISNIS tenant, bukan CURRENT_DATE (zona sesi).
-- Dijalankan di DB scratch (gate_v306.sh). Tanpa migrasi (definisi prod) = KONTROL MERAH -> harus GAGAL.
-- Uji perilaku: zona SESI dipasang agar CURRENT_DATE != tanggal_bisnis (mensimulasikan 00-07 WIB saat
-- server UTC); arah (maju/mundur) dipilih otomatis, harapan dihitung dari tanggal bisnis.
\set ON_ERROR_STOP 1
BEGIN;
DO $$
DECLARE
    fs text[] := ARRAY['get_expiring_batches','get_expired_batches','get_cheque_aging','get_upcoming_cheques',
        'get_item_price','get_maintenance_due','get_recurring_bill_stats','get_current_open_period',
        'mark_serials_sold','auto_expire_batches','check_quote_expiry','process_expired_batches',
        'ship_stock_transfer','receive_stock_transfer','update_sales_invoice_status'];
    f text; src text; n int; salah int := 0;
    T text := 'kaos-biru-konveksi';
    W date; C date; z text; maju boolean;
    v_wh uuid; v_b1 uuid; v_b2 uuid; v_b3 uuid; v_cust uuid; v_q uuid;
    v_int int; v_st text; tn text;
BEGIN
    -- (1) PEMINDAI: tiap fungsi A+B ada tepat satu, tanpa CURRENT_DATE, memakai tanggal_bisnis(
    FOREACH f IN ARRAY fs LOOP
        SELECT count(*) INTO n FROM pg_proc p JOIN pg_namespace s ON s.oid = p.pronamespace
            WHERE s.nspname = 'public' AND p.proname = f;
        IF n <> 1 THEN RAISE WARNING 'GAGAL: % ada % definisi', f, n; salah := salah + 1; CONTINUE; END IF;
        SELECT p.prosrc INTO src FROM pg_proc p JOIN pg_namespace s ON s.oid = p.pronamespace
            WHERE s.nspname = 'public' AND p.proname = f;
        IF src ~* '\mcurrent_date\M' THEN RAISE WARNING 'GAGAL: % masih memakai CURRENT_DATE', f; salah := salah + 1; END IF;
        IF src !~ 'tanggal_bisnis\(' THEN RAISE WARNING 'GAGAL: % tidak memakai tanggal_bisnis(', f; salah := salah + 1; END IF;
    END LOOP;

    -- (2) zona sesi berbeda tanggal dari tanggal bisnis
    W := tanggal_bisnis(T);
    FOREACH z IN ARRAY ARRAY['Etc/GMT-14', 'Etc/GMT+12'] LOOP
        PERFORM set_config('TimeZone', z, true);
        C := CURRENT_DATE;
        EXIT WHEN C <> W;
    END LOOP;
    IF C = W THEN RAISE EXCEPTION 'GAGAL-ALAT: tak ada zona yang membuat CURRENT_DATE != tanggal bisnis'; END IF;
    maju := C > W;
    RAISE NOTICE 'zona sesi % : CURRENT_DATE % vs tanggal bisnis % (%)', z, C, W, CASE WHEN maju THEN 'maju' ELSE 'mundur' END;

    -- data uji (scratch)
    INSERT INTO warehouses (tenant_id, code, name) VALUES (T, 'GV306', 'Gudang V306') RETURNING id INTO v_wh;
    INSERT INTO item_batches (tenant_id, item_id, batch_number, initial_quantity, current_quantity, expiry_date, status)
        VALUES (T, gen_random_uuid(), 'V306-HARIINI', 5, 5, W, 'active') RETURNING id INTO v_b1;
    INSERT INTO item_batches (tenant_id, item_id, batch_number, initial_quantity, current_quantity, expiry_date, status)
        VALUES (T, gen_random_uuid(), 'V306-KEMARIN', 5, 5, W - 1, 'active') RETURNING id INTO v_b2;
    INSERT INTO batch_warehouse_stock (tenant_id, batch_id, warehouse_id, quantity) VALUES (T, v_b1, v_wh, 5), (T, v_b2, v_wh, 5);

    -- (3) get_expiring_batches: kedaluwarsa HARI INI (bisnis) -> days_until_expiry = 0
    SELECT days_until_expiry INTO v_int FROM get_expiring_batches(T, 30, NULL) WHERE batch_id = v_b1;
    IF v_int IS DISTINCT FROM 0 THEN RAISE WARNING 'GAGAL: get_expiring_batches days_until_expiry=% (harus 0)', v_int; salah := salah + 1; END IF;

    -- (4) get_expired_batches: kedaluwarsa KEMARIN (bisnis) -> ada, days_expired = 1; HARI INI -> tidak ada
    SELECT days_expired INTO v_int FROM get_expired_batches(T, NULL) WHERE batch_id = v_b2;
    IF v_int IS DISTINCT FROM 1 THEN RAISE WARNING 'GAGAL: get_expired_batches kemarin days_expired=% (harus 1)', v_int; salah := salah + 1; END IF;
    PERFORM 1 FROM get_expired_batches(T, NULL) WHERE batch_id = v_b1;
    IF FOUND THEN RAISE WARNING 'GAGAL: get_expired_batches memuat batch yang kedaluwarsa HARI INI'; salah := salah + 1; END IF;

    -- (5) trigger auto_expire_batches: batch pembeda (maju: hari ini -> tetap active; mundur: kemarin -> expired)
    INSERT INTO item_batches (tenant_id, item_id, batch_number, initial_quantity, current_quantity, expiry_date, status)
        VALUES (T, gen_random_uuid(), 'V306-TRG', 5, 5, CASE WHEN maju THEN W ELSE W - 1 END, 'active') RETURNING id INTO v_b3;
    UPDATE item_batches SET current_quantity = current_quantity WHERE id = v_b3;
    SELECT status INTO v_st FROM item_batches WHERE id = v_b3;
    IF v_st IS DISTINCT FROM (CASE WHEN maju THEN 'active' ELSE 'expired' END) THEN
        RAISE WARNING 'GAGAL: auto_expire_batches status=% (harus %)', v_st, CASE WHEN maju THEN 'active' ELSE 'expired' END; salah := salah + 1; END IF;

    -- (6) trigger check_quote_expiry (quotes = tabel ber-data)
    INSERT INTO customers (tenant_id, nama) VALUES (T, 'Pelanggan V306') RETURNING id INTO v_cust;
    INSERT INTO quotes (tenant_id, quote_number, quote_date, customer_id, customer_name, expiry_date, status)
        VALUES (T, 'QV306-0001', W - 10, v_cust, 'Pelanggan V306', CASE WHEN maju THEN W ELSE W - 1 END, 'sent') RETURNING id INTO v_q;
    UPDATE quotes SET notes = 'sentuh' WHERE id = v_q;
    SELECT status INTO v_st FROM quotes WHERE id = v_q;
    IF v_st IS DISTINCT FROM (CASE WHEN maju THEN 'sent' ELSE 'expired' END) THEN
        RAISE WARNING 'GAGAL: check_quote_expiry status=% (harus %)', v_st, CASE WHEN maju THEN 'sent' ELSE 'expired' END; salah := salah + 1; END IF;

    -- (7) process_expired_batches: per tenant; batch kedaluwarsa HARI INI tak boleh ikut
    PERFORM * FROM process_expired_batches();
    SELECT status INTO v_st FROM item_batches WHERE id = v_b1;
    IF v_st <> 'active' THEN RAISE WARNING 'GAGAL: process_expired_batches meng-expire batch HARI INI (status %)', v_st; salah := salah + 1; END IF;
    SELECT status INTO v_st FROM item_batches WHERE id = v_b2;
    IF v_st <> 'expired' THEN RAISE WARNING 'GAGAL: process_expired_batches tak meng-expire batch KEMARIN (status %)', v_st; salah := salah + 1; END IF;

    -- (8) eksekusi nyata fungsi baca lain (yang sehat) untuk semua tenant (SQL-nya benar-benar dijalankan)
    FOR tn IN SELECT id FROM "Tenant" LOOP
        -- get_cheque_aging / get_upcoming_cheques / get_recurring_bill_stats SENGAJA tak dieksekusi:
        -- GALAT di prod SEBELUM V306 (drift skema, tak terkait tanggal; tiket terpisah, putusan MASTER 25 Sep).
        -- Ketiganya tetap dijaga pemindai (1) dari CURRENT_DATE.
        PERFORM * FROM get_item_price(tn, gen_random_uuid(), NULL, 1, NULL);
        PERFORM * FROM get_maintenance_due(tn, 30);
        PERFORM * FROM get_current_open_period(tn);
    END LOOP;
    PERFORM mark_serials_sold(T, ARRAY[]::uuid[], NULL, NULL, NULL, NULL);

    IF salah > 0 THEN RAISE EXCEPTION 'GAGAL: % pelanggaran', salah; END IF;
    RAISE NOTICE 'LULUS: V306 15 fungsi bertanggal bisnis (pemindai + perilaku zona % + eksekusi semua tenant)', z;
END $$;
ROLLBACK;
