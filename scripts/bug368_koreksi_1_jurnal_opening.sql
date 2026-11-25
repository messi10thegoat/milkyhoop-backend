-- BUG-368 (1): jurnal OPENING 128.000 untuk baris OB "OB-Cm20s - 2" kaos. UNTUK DITINJAU MASTER; JANGAN eksekusi sebelum MASTER OK.
-- Meniru catat_saldo_awal_per_gudang (services/saldo_awal_stok.py) TANPA baris ledger baru (baris OB sudah ada) + isi journal_id NULL->nilai (carve-out Rule 8).
-- Transaksi tunggal, berhenti (RAISE) bila prasyarat meleset. Default berakhir ROLLBACK; ganti ke COMMIT hanya setelah keluaran sebelum/sesudah ditinjau.
BEGIN;
SELECT pg_advisory_xact_lock(hashtext('OB_STOK:kaos-biru-konveksi'));
\echo == SEBELUM
SELECT count(*) AS ledger_ob_tanpa_jurnal FROM inventory_ledger WHERE tenant_id='kaos-biru-konveksi' AND source_number='OB-Cm20s - 2' AND journal_id IS NULL;
DO $$
DECLARE l record; v_ek uuid; v_inv uuid; v_j uuid; v_no text; v_n int; v_nilai numeric;
BEGIN
  SELECT * INTO l FROM inventory_ledger WHERE tenant_id='kaos-biru-konveksi' AND source_number='OB-Cm20s - 2' AND journal_id IS NULL;
  IF NOT FOUND THEN RAISE EXCEPTION 'baris OB tak ditemukan / sudah berjurnal'; END IF;
  IF (SELECT count(*) FROM inventory_ledger WHERE tenant_id='kaos-biru-konveksi' AND source_number='OB-Cm20s - 2') <> 1 THEN RAISE EXCEPTION 'baris OB tidak tunggal'; END IF;
  IF EXISTS (SELECT 1 FROM journal_entries WHERE tenant_id='kaos-biru-konveksi' AND source_type='OPENING' AND source_id::text=l.product_id::text AND status='POSTED' AND reversed_by_id IS NULL) THEN RAISE EXCEPTION 'barang sudah punya jurnal saldo awal'; END IF;
  IF EXISTS (SELECT 1 FROM fiscal_periods WHERE tenant_id='kaos-biru-konveksi' AND start_date<=l.movement_date AND end_date>=l.movement_date AND status<>'OPEN') THEN RAISE EXCEPTION 'periode tertutup'; END IF;
  v_nilai := l.quantity_in * l.unit_cost;
  IF v_nilai <> 128000 THEN RAISE EXCEPTION 'nilai % bukan 128000', v_nilai; END IF;
  SELECT id INTO v_inv FROM chart_of_accounts WHERE tenant_id='kaos-biru-konveksi' AND account_code='1-10600';
  -- akun ekuitas saldo awal = akun kredit jurnal OPENING kaos yang sudah ada (harus tunggal)
  SELECT DISTINCT jl.account_id INTO STRICT v_ek FROM journal_lines jl JOIN journal_entries je ON je.id=jl.journal_id
   JOIN chart_of_accounts c ON c.id=jl.account_id
   WHERE je.tenant_id='kaos-biru-konveksi' AND je.source_type='OPENING' AND jl.credit>0 AND c.account_type='EQUITY';
  SELECT count(*) INTO v_n FROM journal_entries WHERE tenant_id='kaos-biru-konveksi' AND journal_number LIKE 'OB-S-'||to_char(l.movement_date,'YYMMDD')||'%';
  v_no := 'OB-S-'||to_char(l.movement_date,'YYMMDD')||'-'||lpad((v_n+1)::text,3,'0');
  INSERT INTO journal_entries (tenant_id, journal_number, journal_date, description, source_type, source_id, total_debit, total_credit, status, is_opening_balance)
    VALUES ('kaos-biru-konveksi', v_no, l.movement_date, 'Saldo awal persediaan - '||l.product_name||' (koreksi BUG-368)', 'OPENING', l.product_id, v_nilai, v_nilai, 'DRAFT', true) RETURNING id INTO v_j;
  INSERT INTO journal_lines (journal_id, line_number, account_id, debit, credit, memo) VALUES (v_j,1,v_inv,v_nilai,0,'Saldo awal persediaan - '||l.product_name);
  INSERT INTO journal_lines (journal_id, line_number, account_id, debit, credit, memo) VALUES (v_j,2,v_ek,0,v_nilai,'Modal saldo awal - '||l.product_name);
  UPDATE journal_entries SET status='POSTED' WHERE id=v_j AND tenant_id='kaos-biru-konveksi';
  UPDATE inventory_ledger SET journal_id=v_j WHERE id=l.id AND journal_id IS NULL;   -- NULL -> nilai SAJA (Rule 8 carve-out)
  RAISE NOTICE 'jurnal % dibuat (%)', v_no, v_j;
END $$;
\echo == SESUDAH (selisih ledger-GL kaos harus = 0,16 + 80006 sebelum void bill = 80006,16 → setelah (1) saja: -80005,84)
SELECT (SELECT sum((quantity_in-quantity_out)*unit_cost) FROM inventory_ledger WHERE tenant_id='kaos-biru-konveksi')
     - (SELECT sum(jl.debit-jl.credit) FROM journal_lines jl JOIN journal_entries je ON je.id=jl.journal_id JOIN chart_of_accounts c ON c.id=jl.account_id
        WHERE je.tenant_id='kaos-biru-konveksi' AND je.status='POSTED' AND c.account_code='1-10600') AS selisih_ledger_gl;
ROLLBACK;  -- ganti ke COMMIT hanya setelah MASTER OK
