-- ============================================================================
-- audit_stock_trigger_functional.sql
--
-- Uji FUNGSIONAL trigger stok inventory (bukan inspeksi statik). Ada karena
-- V204 LOLOS grep kolom-fiktif — kolomnya benar, pola upsert-nya yang salah,
-- dan hanya eksekusi 2-arah nyata yang menangkapnya. Melengkapi
-- audit_insert_schema_drift.py (statik) dengan cek dinamis.
--
-- Semua di dalam transaksi + ROLLBACK: TIDAK mengubah data.
-- Idempoten, aman dijalankan kapan saja di DB yang punya >=1 baris warehouse_stock.
-- Keluaran: 5 baris 'PASS' bila sehat; 'FAIL' bila trigger regresi.
-- ============================================================================
\pset pager off
BEGIN;
DO $audit$
DECLARE
    v_t TEXT; v_w UUID; v_p UUID; v_new_p UUID; v_q NUMERIC; v_start NUMERIC;
    v_fail INT := 0;
BEGIN
    SELECT ws.tenant_id, ws.warehouse_id, ws.item_id, ws.quantity
      INTO v_t, v_w, v_p, v_start
      FROM warehouse_stock ws WHERE ws.quantity >= 50 LIMIT 1;
    IF v_t IS NULL THEN RAISE NOTICE 'SKIP: tidak ada baris warehouse_stock >=50 untuk difixture'; RETURN; END IF;

    -- 1) INBOUND
    INSERT INTO inventory_ledger (id,tenant_id,product_id,product_code,product_name,movement_type,movement_date,source_type,source_id,source_number,quantity_in,quantity_out,quantity_balance,unit_cost,total_cost,average_cost,warehouse_id)
      VALUES (gen_random_uuid(),v_t,v_p,'A','a','ADJUSTMENT',CURRENT_DATE,'STOCK_ADJUSTMENT',gen_random_uuid(),'A',10,0,0,1,1,1,v_w);
    v_q := (SELECT quantity FROM warehouse_stock WHERE tenant_id=v_t AND warehouse_id=v_w AND item_id=v_p);
    IF v_q = v_start+10 THEN RAISE NOTICE 'PASS inbound +10'; ELSE RAISE NOTICE 'FAIL inbound (% != %)', v_q, v_start+10; v_fail:=v_fail+1; END IF;

    -- 2) OUTBOUND (baris ada) — regresi V204 kalau gagal
    INSERT INTO inventory_ledger (id,tenant_id,product_id,product_code,product_name,movement_type,movement_date,source_type,source_id,source_number,quantity_in,quantity_out,quantity_balance,unit_cost,total_cost,average_cost,warehouse_id)
      VALUES (gen_random_uuid(),v_t,v_p,'A','a','MATERIAL_ISSUE',CURRENT_DATE,'MATERIAL_ISSUE',gen_random_uuid(),'B',0,30,0,1,1,1,v_w);
    v_q := (SELECT quantity FROM warehouse_stock WHERE tenant_id=v_t AND warehouse_id=v_w AND item_id=v_p);
    IF v_q = v_start-20 THEN RAISE NOTICE 'PASS outbound -30 (baris ada)'; ELSE RAISE NOTICE 'FAIL outbound (%)', v_q; v_fail:=v_fail+1; END IF;

    -- 3) OVER-ISSUE (Law 13) — harus ditolak
    BEGIN
        INSERT INTO inventory_ledger (id,tenant_id,product_id,product_code,product_name,movement_type,movement_date,source_type,source_id,source_number,quantity_in,quantity_out,quantity_balance,unit_cost,total_cost,average_cost,warehouse_id)
          VALUES (gen_random_uuid(),v_t,v_p,'A','a','MATERIAL_ISSUE',CURRENT_DATE,'MATERIAL_ISSUE',gen_random_uuid(),'C',0,v_start+9999,0,1,1,1,v_w);
        RAISE NOTICE 'FAIL over-issue LOLOS (Law 13 bocor)'; v_fail:=v_fail+1;
    EXCEPTION WHEN check_violation THEN RAISE NOTICE 'PASS over-issue ditolak (Law 13)'; END;

    -- 4) ITEM NOL-STOK keluar — harus ditolak
    INSERT INTO products (id,tenant_id,nama_produk,item_type,satuan,track_inventory)
      VALUES (gen_random_uuid(),v_t,'AuditNolStok','goods','pcs',true) RETURNING id INTO v_new_p;
    BEGIN
        INSERT INTO inventory_ledger (id,tenant_id,product_id,product_code,product_name,movement_type,movement_date,source_type,source_id,source_number,quantity_in,quantity_out,quantity_balance,unit_cost,total_cost,average_cost,warehouse_id)
          VALUES (gen_random_uuid(),v_t,v_new_p,'B','b','MATERIAL_ISSUE',CURRENT_DATE,'MATERIAL_ISSUE',gen_random_uuid(),'D',0,10,0,1,1,1,v_w);
        RAISE NOTICE 'FAIL item nol-stok outflow LOLOS'; v_fail:=v_fail+1;
    EXCEPTION WHEN check_violation THEN RAISE NOTICE 'PASS item nol-stok outflow ditolak'; END;

    -- 5) ITEM NOL-STOK masuk — harus bikin baris
    INSERT INTO inventory_ledger (id,tenant_id,product_id,product_code,product_name,movement_type,movement_date,source_type,source_id,source_number,quantity_in,quantity_out,quantity_balance,unit_cost,total_cost,average_cost,warehouse_id)
      VALUES (gen_random_uuid(),v_t,v_new_p,'B','b','ADJUSTMENT',CURRENT_DATE,'STOCK_ADJUSTMENT',gen_random_uuid(),'E',25,0,0,1,1,1,v_w);
    IF (SELECT quantity FROM warehouse_stock WHERE tenant_id=v_t AND item_id=v_new_p)=25
      THEN RAISE NOTICE 'PASS item baru inbound bikin baris'; ELSE RAISE NOTICE 'FAIL item baru inbound'; v_fail:=v_fail+1; END IF;

    IF v_fail=0 THEN RAISE NOTICE '=== SEMUA 5 LULUS: trigger stok sehat 2-arah ==='; 
    ELSE RAISE EXCEPTION '=== % FAIL: trigger stok REGRESI ===', v_fail; END IF;
END $audit$;
ROLLBACK;
