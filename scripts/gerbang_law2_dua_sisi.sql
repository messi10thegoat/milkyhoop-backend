-- GERBANG Law 2 — SETIA pada urutan produksi. Nol baris menetap (ROLLBACK).
--
-- Urutan produksi (expenses.py ~2020-2066), ditiru PERSIS:
--   1. INSERT pembalik sebagai 'DRAFT'      (trigger TIDAK menyala: status != POSTED)
--   2. UPDATE pembalik -> 'POSTED'          (peristiwa trigger DIANTRE)
--   3. UPDATE asli -> 'VOID'                (hanya di sisi MERAH)
--   4. COMMIT / SET CONSTRAINTS IMMEDIATE   (trigger MENYALA)
--
-- Gerbang pertamaku TIDAK merah karena aku menyisipkan pembalik langsung
-- sebagai POSTED (satu peristiwa) alih-alih DRAFT lalu dinaikkan (dua
-- peristiwa) -- itu mengubah KAPAN peristiwa trigger diantre. Kesetiaan pada
-- bentuk produksi bukan kerapian; ia yang menentukan gerbang ini menguji cacat
-- KITA atau cacat karangan.

\set TENANT 'kaos-biru-konveksi'

-- ========================================================== SISI MERAH
BEGIN;
SET CONSTRAINTS ALL DEFERRED;
SELECT set_config('app.tenant_id', :'TENANT', true);

\echo '════════ SISI MERAH — perilaku LAMA (asli dibalik ke VOID) ════════'

INSERT INTO journal_entries
    (id, tenant_id, journal_number, journal_date, description,
     source_type, source_id, status, total_debit, total_credit)
VALUES ('aaaaaaaa-0000-0000-0000-000000000001', :'TENANT',
        'SETIA-ASLI-M', CURRENT_DATE, 'gerbang setia (ROLLBACK)',
        'EXPENSE', gen_random_uuid(), 'POSTED', 1, 1);
SET CONSTRAINTS ALL IMMEDIATE;          -- asli diposting di transaksi "sebelumnya"
-- WAJIB: IMMEDIATE bukan sekali-pakai, ia mengubah MODE untuk sisa transaksi.
-- Tanpa baris ini, trigger pembalik menyala di waktu-pernyataan (sebelum flip
-- VOID) dan gerbang membuktikan keadaan yang produksi TIDAK punya.
SET CONSTRAINTS ALL DEFERRED;

-- 1. pembalik lahir sebagai DRAFT
INSERT INTO journal_entries
    (id, tenant_id, journal_number, journal_date, description,
     source_type, source_id, status, total_debit, total_credit, reversal_of_id)
VALUES ('bbbbbbbb-0000-0000-0000-000000000001', :'TENANT',
        'SETIA-REV-M', CURRENT_DATE, 'gerbang setia (ROLLBACK)',
        'EXPENSE_REVERSAL', gen_random_uuid(), 'DRAFT', 1, 1,
        'aaaaaaaa-0000-0000-0000-000000000001');

-- 2. Law 20: DRAFT -> POSTED
UPDATE journal_entries SET status = 'POSTED'
 WHERE id = 'bbbbbbbb-0000-0000-0000-000000000001';

-- 3. Law 26 + pembalikan status (INI yang unit ini cabut)
UPDATE journal_entries
   SET reversed_by_id = 'bbbbbbbb-0000-0000-0000-000000000001', status = 'VOID'
 WHERE id = 'aaaaaaaa-0000-0000-0000-000000000001';

-- 4. COMMIT -> trigger menyala
SET CONSTRAINTS ALL IMMEDIATE;

SELECT journal_number, status, chain_sequence FROM journal_entries
WHERE id IN ('aaaaaaaa-0000-0000-0000-000000000001',
             'bbbbbbbb-0000-0000-0000-000000000001')
ORDER BY journal_number;

SELECT CASE WHEN a.chain_sequence = r.chain_sequence
            THEN '[M] MERAH TERBUKTI — tabrakan di ' || a.chain_sequence
            ELSE '[!] GERBANG TAK SAH: tak bertabrakan (' ||
                 a.chain_sequence || ' vs ' || r.chain_sequence ||
                 ') — simulasi belum setia pada produksi'
       END AS sisi_merah
FROM journal_entries a, journal_entries r
WHERE a.id = 'aaaaaaaa-0000-0000-0000-000000000001'
  AND r.id = 'bbbbbbbb-0000-0000-0000-000000000001';

ROLLBACK;

-- ========================================================== SISI HIJAU
BEGIN;
SET CONSTRAINTS ALL DEFERRED;
SELECT set_config('app.tenant_id', :'TENANT', true);

\echo '════════ SISI HIJAU — perilaku BARU (asli TETAP POSTED) ════════'

INSERT INTO journal_entries
    (id, tenant_id, journal_number, journal_date, description,
     source_type, source_id, status, total_debit, total_credit)
VALUES ('cccccccc-0000-0000-0000-000000000001', :'TENANT',
        'SETIA-ASLI-H', CURRENT_DATE, 'gerbang setia (ROLLBACK)',
        'EXPENSE', gen_random_uuid(), 'POSTED', 1, 1);
SET CONSTRAINTS ALL IMMEDIATE;
SET CONSTRAINTS ALL DEFERRED;   -- lihat catatan di sisi merah

INSERT INTO journal_entries
    (id, tenant_id, journal_number, journal_date, description,
     source_type, source_id, status, total_debit, total_credit, reversal_of_id)
VALUES ('dddddddd-0000-0000-0000-000000000001', :'TENANT',
        'SETIA-REV-H', CURRENT_DATE, 'gerbang setia (ROLLBACK)',
        'EXPENSE_REVERSAL', gen_random_uuid(), 'DRAFT', 1, 1,
        'cccccccc-0000-0000-0000-000000000001');

UPDATE journal_entries SET status = 'POSTED'
 WHERE id = 'dddddddd-0000-0000-0000-000000000001';

-- perilaku BARU: reversed_by_id + reversed_at, status TIDAK disentuh.
-- Bentuk ini HARUS identik dengan yang dikirim ke produksi; kalau gerbang
-- menguji pernyataan yang lebih ramping daripada yang di-deploy, ia
-- membuktikan klaim yang lebih sempit daripada yang kuucapkan.
UPDATE journal_entries
   SET reversed_by_id = 'dddddddd-0000-0000-0000-000000000001',
       reversed_at = NOW()
 WHERE id = 'cccccccc-0000-0000-0000-000000000001';

SET CONSTRAINTS ALL IMMEDIATE;

SELECT journal_number, status, chain_sequence FROM journal_entries
WHERE id IN ('cccccccc-0000-0000-0000-000000000001',
             'dddddddd-0000-0000-0000-000000000001')
ORDER BY journal_number;

SELECT CASE WHEN r.chain_sequence = a.chain_sequence + 1
            THEN '[H] HIJAU — pembalik = asli+1 (' || a.chain_sequence ||
                 ' -> ' || r.chain_sequence || '), NOL tabrakan'
            WHEN r.chain_sequence = a.chain_sequence
            THEN '[X] MASIH MERAH — tabrakan di ' || a.chain_sequence
            ELSE '[?] tak terduga: ' || a.chain_sequence || ' vs ' || r.chain_sequence
       END AS sisi_hijau
FROM journal_entries a, journal_entries r
WHERE a.id = 'cccccccc-0000-0000-0000-000000000001'
  AND r.id = 'dddddddd-0000-0000-0000-000000000001';

-- asli WAJIB tetap POSTED, tertandai, DAN ber-reversed_at.
-- reversed_at di-ASSERT, bukan sekadar dijalankan: kalau ia cuma dieksekusi,
-- gerbang tetap hijau saat penulisannya hilang -- persis perubahan yang
-- ditambahkan pass ini. Gerbang yang tak bisa merah utk perubahannya sendiri
-- tidak menutupinya.
SELECT CASE
    WHEN status = 'POSTED' AND reversed_by_id IS NOT NULL AND reversed_at IS NOT NULL
        THEN '[H] asli: POSTED + tertandai + reversed_at TERISI'
    WHEN reversed_at IS NULL
        THEN '[X] MERAH: reversed_at NULL — Law 26 separuh ditulis'
    WHEN status <> 'POSTED'
        THEN '[X] MERAH: status asli ' || status || ' — seharusnya POSTED'
    ELSE '[X] MERAH: reversed_by_id kosong'
END AS periksa_asli
FROM journal_entries WHERE id = 'cccccccc-0000-0000-0000-000000000001';

ROLLBACK;

-- ========================================================== KONTROL
\echo '════════ KONTROL — nol sentinel menetap, keadaan tak bergerak ════════'
SELECT count(*) AS sentinel_menetap FROM journal_entries
WHERE journal_number LIKE 'SETIA-%';

SELECT max(chain_sequence) AS max_seq FROM journal_entries
WHERE tenant_id = 'kaos-biru-konveksi' AND status = 'POSTED';

SELECT count(*) AS pasangan_ganda FROM (
    SELECT chain_sequence FROM journal_entries
    WHERE tenant_id = 'kaos-biru-konveksi'
    GROUP BY 1 HAVING count(*) > 1) x;

SELECT count(*) FILTER (WHERE NOT is_valid) AS hash_pecah
FROM verify_chain_integrity('kaos-biru-konveksi');
