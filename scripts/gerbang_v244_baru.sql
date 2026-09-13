-- FASE BARU (V244 terpasang di transaksi ini).
\set ON_ERROR_STOP 1

-- daftar kolom yang DIHARAPKAN, ditulis ULANG di sini secara independen dari migrasi
CREATE TEMP TABLE harap_kolom (t text, kol text);
INSERT INTO harap_kolom VALUES
 ('sales_invoices','subtotal'),('sales_invoices','discount_percent'),('sales_invoices','discount_amount'),
 ('sales_invoices','tax_rate'),('sales_invoices','tax_amount'),('sales_invoices','total_amount'),
 ('bills','amount'),('bills','subtotal'),('bills','tax_rate'),('bills','tax_amount'),('bills','dpp'),
 ('bills','dpp_manual'),('bills','grand_total'),('bills','invoice_discount_percent'),
 ('bills','invoice_discount_amount'),('bills','invoice_discount_total'),('bills','cash_discount_percent'),
 ('bills','cash_discount_amount'),('bills','cash_discount_total'),('bills','item_discount_total'),
 ('bills','pph_rate'),('bills','pph_amount'),('bills','pph_dpp'),
 ('expenses','subtotal'),('expenses','tax_rate'),('expenses','tax_amount'),('expenses','pph_rate'),
 ('expenses','pph_amount'),('expenses','total_amount'),
 ('receive_payments','total_amount'),('receive_payments','discount_amount'),
 ('bill_payments_v2','total_amount'),('bill_payments_v2','discount_amount'),('bill_payments_v2','bank_fee_amount'),
 ('bill_payments_v2','pph_amount'),('bill_payments_v2','exchange_rate'),('bill_payments_v2','amount_in_base_currency');

-- kolom yang BENAR-BENAR terpasang di trigger (dibaca dari katalog)
CREATE TEMP TABLE pasang_kolom AS
SELECT c.relname AS t, unnest(string_to_array(rtrim(encode(tg.tgargs, 'escape'), E'\\000'), E'\\000')) AS kol
FROM pg_trigger tg JOIN pg_class c ON c.oid = tg.tgrelid
WHERE tg.tgname = 'trg_law19_bekukan_nominal' AND NOT tg.tgisinternal;

INSERT INTO hasil (sisi, uji, hasil, harap)
SELECT 'G2 daftar', 'kolom terpasang = kolom diharapkan (selisih dua arah)',
       CASE WHEN NOT EXISTS (SELECT t,kol FROM harap_kolom EXCEPT SELECT t,kol FROM pasang_kolom)
             AND NOT EXISTS (SELECT t,kol FROM pasang_kolom EXCEPT SELECT t,kol FROM harap_kolom)
            THEN 'sama' ELSE 'BEDA' END, 'sama';
INSERT INTO hasil (sisi, uji, hasil, harap)
SELECT 'G2 daftar', 'cacah trigger terpasang', count(*)::text, '5'
FROM pg_trigger WHERE tgname = 'trg_law19_bekukan_nominal' AND NOT tgisinternal;

-- G2: tiap kolom yang DIHARAPKAN, sendiri-sendiri -> ditolak
SELECT pg_temp.coba('G2 tolak-per-kolom', h.t || '.' || h.kol || ' +1',
       format('UPDATE %I SET %I = COALESCE(%I, 0) + 1 WHERE id = %L', h.t, h.kol, h.kol, s.id), 'ditolak')
FROM harap_kolom h JOIN subjek s ON s.t = h.t ORDER BY h.t, h.kol;

-- G2: journal_id -> NULL ditolak, per tabel
SELECT pg_temp.coba('G2 tolak-journal_id', s.t || '.journal_id -> NULL',
       format('UPDATE %I SET journal_id = NULL WHERE id = %L', s.t, s.id), 'ditolak')
FROM subjek s ORDER BY s.t;

-- T1: nilai SAMA, skala beda -> lolos (wakil + kolom persen/rate)
SELECT pg_temp.coba('T1 skala-sama', w.t || '.' || w.kol || ' ::numeric(30,8)',
       format('UPDATE %I SET %I = (%I)::numeric(30,8) WHERE id = %L', w.t, w.kol, w.kol, s.id), 'lolos')
FROM wakil w JOIN subjek s ON s.t = w.t ORDER BY w.t;
SELECT pg_temp.coba('T1 skala-sama', 'bills.tax_rate ::numeric(30,8)',
       format('UPDATE bills SET tax_rate = (tax_rate)::numeric(30,8) WHERE id = %L', s.id), 'lolos')
FROM subjek s WHERE s.t = 'bills';

-- T2: hanya updated_at -> lolos
SELECT pg_temp.coba('T2 non-nominal', s.t || '.updated_at = now()',
       format('UPDATE %I SET updated_at = now() WHERE id = %L', s.t, s.id), 'lolos')
FROM subjek s ORDER BY s.t;

-- G3a: himpunan SET pasca-posting dari enumerasi (nilai diubah sungguh bila aman,
-- kolom status diisi nilainya sendiri supaya validator status tak ikut menilai)
SELECT pg_temp.coba('G3a pasca-posting', x.t || ': ' || x.label, format(x.sqlt, s.id), 'lolos')
FROM (VALUES
 ('sales_invoices','bayar: amount_paid,status,updated_at',
  'UPDATE sales_invoices SET amount_paid = COALESCE(amount_paid,0) + 1, status = status, updated_at = now() WHERE id = %L'),
 ('sales_invoices','fulfill: fulfillment/revenue/qty/recognized',
  'UPDATE sales_invoices SET fulfillment_status = fulfillment_status, revenue_status = revenue_status, total_fulfilled_qty = COALESCE(total_fulfilled_qty,0) + 1, total_recognized_amount = COALESCE(total_recognized_amount,0) + 1 WHERE id = %L'),
 ('sales_invoices','void: accounting/operational/voided_at/voided_reason',
  'UPDATE sales_invoices SET accounting_status = accounting_status, operational_status = operational_status, voided_at = now(), voided_reason = ''gerbang'' WHERE id = %L'),
 ('sales_invoices','merge: customer_id',
  'UPDATE sales_invoices SET customer_id = customer_id WHERE id = %L'),
 ('sales_invoices','total_cogs',
  'UPDATE sales_invoices SET total_cogs = COALESCE(total_cogs,0) + 1 WHERE id = %L'),
 ('bills','bayar: amount_paid,status,status_v2,updated_at',
  'UPDATE bills SET amount_paid = COALESCE(amount_paid,0) + 1, status = status, status_v2 = status_v2, updated_at = now() WHERE id = %L'),
 ('bills','void: voided_at/voided_reason/accounting/operational',
  'UPDATE bills SET voided_at = now(), voided_reason = ''gerbang'', accounting_status = accounting_status, operational_status = operational_status WHERE id = %L'),
 ('bills','merge: vendor_id,vendor_name',
  'UPDATE bills SET vendor_id = vendor_id, vendor_name = vendor_name WHERE id = %L'),
 ('expenses','void: status,accounting,operational,notes',
  'UPDATE expenses SET status = status, accounting_status = accounting_status, operational_status = operational_status, notes = COALESCE(notes,'''') || '' '', updated_at = now() WHERE id = %L'),
 ('expenses','has_receipt',
  'UPDATE expenses SET has_receipt = has_receipt, updated_at = now() WHERE id = %L'),
 ('expenses','merge: vendor_id,vendor_name',
  'UPDATE expenses SET vendor_id = vendor_id, vendor_name = vendor_name WHERE id = %L'),
 ('receive_payments','void: status,void_*,voided_*',
  'UPDATE receive_payments SET status = status, void_reason = ''gerbang'', voided_at = now() WHERE id = %L'),
 ('receive_payments','bank_transaction_id, created_deposit_id, journal_number, posted_*',
  'UPDATE receive_payments SET bank_transaction_id = bank_transaction_id, created_deposit_id = created_deposit_id, journal_number = journal_number, posted_at = posted_at, posted_by = posted_by WHERE id = %L'),
 ('receive_payments','alokasi: allocated_amount, unapplied_amount',
  'UPDATE receive_payments SET allocated_amount = COALESCE(allocated_amount,0) + 1, unapplied_amount = COALESCE(unapplied_amount,0) - 1 WHERE id = %L'),
 ('receive_payments','merge: customer_id',
  'UPDATE receive_payments SET customer_id = customer_id WHERE id = %L'),
 ('bill_payments_v2','void: status,void_*,voided_*',
  'UPDATE bill_payments_v2 SET status = status, void_reason = ''gerbang'', voided_at = now(), updated_at = now() WHERE id = %L'),
 ('bill_payments_v2','journal_number, posted_*',
  'UPDATE bill_payments_v2 SET journal_number = journal_number, posted_at = posted_at, posted_by = posted_by WHERE id = %L'),
 ('bill_payments_v2','alokasi: allocated_amount, unapplied_amount',
  'UPDATE bill_payments_v2 SET allocated_amount = COALESCE(allocated_amount,0) + 1, unapplied_amount = COALESCE(unapplied_amount,0) - 1 WHERE id = %L'),
 ('bill_payments_v2','merge: vendor_id,vendor_name',
  'UPDATE bill_payments_v2 SET vendor_id = vendor_id, vendor_name = vendor_name WHERE id = %L')
) x(t, label, sqlt) JOIN subjek s ON s.t = x.t;

-- G3c: draf tetap bisa diubah nominalnya
SELECT pg_temp.coba('G3c draf', t || ' draf: nominal +1',
       format('UPDATE %I SET %I = COALESCE(%I,0) + 1 WHERE id = %L', t, kol, kol, id), 'lolos')
FROM (SELECT 'sales_invoices' t, 'total_amount' kol, (SELECT id FROM sales_invoices WHERE journal_id IS NULL ORDER BY id LIMIT 1) id
      UNION ALL SELECT 'bills', 'grand_total', (SELECT id FROM bills WHERE journal_id IS NULL ORDER BY id LIMIT 1)) d
WHERE id IS NOT NULL;

-- G4: SABOTASE — trigger bills dipasang ulang TANPA grand_total -> grand_total harus LOLOS
SAVEPOINT g4;
DROP TRIGGER trg_law19_bekukan_nominal ON bills;
CREATE TRIGGER trg_law19_bekukan_nominal BEFORE UPDATE ON bills FOR EACH ROW WHEN (OLD.journal_id IS NOT NULL)
    EXECUTE FUNCTION law19_bekukan_nominal('amount', 'subtotal');
SELECT pg_temp.coba('G4 sabotase', 'bills.grand_total +1 tanpa grand_total di daftar (gerbang HARUS melihat lolos)',
       format('UPDATE bills SET grand_total = grand_total + 1 WHERE id = %L', s.id), 'lolos')
FROM subjek s WHERE s.t = 'bills';
-- tangkap SEBELUM rollback savepoint (catatan di tabel ikut terhapus oleh rollback)
SELECT sisi AS g4_sisi, uji AS g4_uji, hasil AS g4_hasil, harap AS g4_harap FROM hasil ORDER BY urut DESC LIMIT 1 \gset
ROLLBACK TO SAVEPOINT g4;
INSERT INTO hasil (sisi, uji, hasil, harap) VALUES (:'g4_sisi', :'g4_uji', :'g4_hasil', :'g4_harap');
-- kontrol: trigger bills ASLI kembali sesudah rollback savepoint -> grand_total ditolak lagi
SELECT pg_temp.coba('G4 pulih', 'bills.grand_total +1 sesudah sabotase dibatalkan',
       format('UPDATE bills SET grand_total = grand_total + 1 WHERE id = %L', s.id), 'ditolak')
FROM subjek s WHERE s.t = 'bills';

-- ROLLBACK V244 terbukti: badan rollback dijalankan dalam savepoint -> ubah nominal lolos lagi
SAVEPOINT rb;
DROP TRIGGER IF EXISTS trg_law19_bekukan_nominal ON sales_invoices;
DROP TRIGGER IF EXISTS trg_law19_bekukan_nominal ON bills;
DROP TRIGGER IF EXISTS trg_law19_bekukan_nominal ON expenses;
DROP TRIGGER IF EXISTS trg_law19_bekukan_nominal ON receive_payments;
DROP TRIGGER IF EXISTS trg_law19_bekukan_nominal ON bill_payments_v2;
DROP FUNCTION IF EXISTS law19_bekukan_nominal();
SELECT pg_temp.coba('RB rollback-V244', w.t || '.' || w.kol || ' +1 sesudah rollback',
       format('UPDATE %I SET %I = %I + 1 WHERE id = %L', w.t, w.kol, w.kol, s.id), 'lolos')
FROM wakil w JOIN subjek s ON s.t = w.t WHERE w.t = 'bills';
SELECT sisi AS rb_sisi, uji AS rb_uji, hasil AS rb_hasil, harap AS rb_harap FROM hasil ORDER BY urut DESC LIMIT 1 \gset
ROLLBACK TO SAVEPOINT rb;
INSERT INTO hasil (sisi, uji, hasil, harap) VALUES (:'rb_sisi', :'rb_uji', :'rb_hasil', :'rb_harap');

\echo ''
\echo '════════ HASIL ════════'
SELECT CASE WHEN hasil = harap THEN '[H]' ELSE '[X]' END ok, sisi, uji, hasil, harap FROM hasil ORDER BY urut;
SELECT count(*) FILTER (WHERE hasil IS DISTINCT FROM harap) AS gagal, count(*) AS total,
       CASE WHEN count(*) = :harap_cacah THEN 'cacah hasil LENGKAP (' || count(*) || ')'
            ELSE 'GERBANG TAK SAH: cacah=' || count(*) || ' harap=' || :harap_cacah END AS kelengkapan
FROM hasil;
