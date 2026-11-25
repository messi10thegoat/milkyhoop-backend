-- BUG-368 (1) pilihan (a) pemilik: jurnal MANUAL Dr 1-10600 / Cr ekuitas saldo awal 128.000, tanggal HARI INI, + isi journal_id baris OB. UNTUK DITINJAU; default ROLLBACK.
-- CATATAN: penulis jurnal manual resmi (routers/journals.py validate_no_derived_layer_accounts, Law 31 Gate 4) MENOLAK akun persediaan; SQL ini meniru POLA resmi
-- (get_next_journal_number, DRAFT -> baris -> POSTED, trigger rantai hash) tetapi melangkahi guard aplikasi itu. Perlu persetujuan eksplisit untuk pelangkahan tersebut.
BEGIN;
SELECT pg_advisory_xact_lock(hashtext('OB_STOK:kaos-biru-konveksi'));
\echo == SEBELUM
SELECT count(*) AS ledger_ob_tanpa_jurnal FROM inventory_ledger WHERE tenant_id='kaos-biru-konveksi' AND source_number='OB-Cm20s - 2' AND journal_id IS NULL;
DO $$
DECLARE l record; v_ek uuid; v_inv uuid; v_j uuid; v_no text; v_nilai numeric; v_tgl date := DATE '2026-11-25';
BEGIN
  SELECT * INTO l FROM inventory_ledger WHERE tenant_id='kaos-biru-konveksi' AND source_number='OB-Cm20s - 2' AND journal_id IS NULL;
  IF NOT FOUND THEN RAISE EXCEPTION 'baris OB tak ditemukan / sudah berjurnal'; END IF;
  IF (SELECT count(*) FROM inventory_ledger WHERE tenant_id='kaos-biru-konveksi' AND source_number='OB-Cm20s - 2') <> 1 THEN RAISE EXCEPTION 'baris OB tidak tunggal'; END IF;
  IF EXISTS (SELECT 1 FROM fiscal_periods WHERE tenant_id='kaos-biru-konveksi' AND start_date<=v_tgl AND end_date>=v_tgl AND status<>'OPEN') THEN RAISE EXCEPTION 'periode hari ini tertutup'; END IF;
  v_nilai := l.quantity_in * l.unit_cost;
  IF v_nilai <> 128000 THEN RAISE EXCEPTION 'nilai % bukan 128000', v_nilai; END IF;
  SELECT id INTO STRICT v_inv FROM chart_of_accounts WHERE tenant_id='kaos-biru-konveksi' AND account_code='1-10600';
  SELECT DISTINCT jl.account_id INTO STRICT v_ek FROM journal_lines jl JOIN journal_entries je ON je.id=jl.journal_id
    JOIN chart_of_accounts c ON c.id=jl.account_id
   WHERE je.tenant_id='kaos-biru-konveksi' AND je.source_type='OPENING' AND jl.credit>0 AND c.account_type='EQUITY';
  v_no := get_next_journal_number('kaos-biru-konveksi', 'JV', v_tgl);
  INSERT INTO journal_entries (tenant_id, journal_number, journal_date, description, source_type, total_debit, total_credit, status)
    VALUES ('kaos-biru-konveksi', v_no, v_tgl, 'Koreksi BUG-368: saldo awal persediaan OB-Cm20s-2 tanpa jurnal', 'MANUAL', v_nilai, v_nilai, 'DRAFT') RETURNING id INTO v_j;
  INSERT INTO journal_lines (journal_id, line_number, account_id, debit, credit, memo) VALUES (v_j,1,v_inv,v_nilai,0,'Koreksi BUG-368: saldo awal persediaan OB-Cm20s-2 tanpa jurnal');
  INSERT INTO journal_lines (journal_id, line_number, account_id, debit, credit, memo) VALUES (v_j,2,v_ek,0,v_nilai,'Koreksi BUG-368: saldo awal persediaan OB-Cm20s-2 tanpa jurnal');
  UPDATE journal_entries SET status='POSTED' WHERE id=v_j AND tenant_id='kaos-biru-konveksi';
  UPDATE inventory_ledger SET journal_id=v_j WHERE id=l.id AND journal_id IS NULL;   -- NULL -> nilai SAJA (Rule 8 carve-out)
  RAISE NOTICE 'jurnal % dibuat (%)', v_no, v_j;
END $$;
\echo == SESUDAH (selisih ledger-GL kaos; target 0,16)
SELECT (SELECT sum((quantity_in-quantity_out)*unit_cost) FROM inventory_ledger WHERE tenant_id='kaos-biru-konveksi')
     - (SELECT sum(jl.debit-jl.credit) FROM journal_lines jl JOIN journal_entries je ON je.id=jl.journal_id JOIN chart_of_accounts c ON c.id=jl.account_id
        WHERE je.tenant_id='kaos-biru-konveksi' AND je.status='POSTED' AND c.account_code='1-10600') AS selisih_ledger_gl;
ROLLBACK;  -- ganti ke COMMIT hanya setelah OK + konfirmasi pemilik langsung
