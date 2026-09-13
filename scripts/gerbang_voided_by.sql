-- GERBANG voided_by/void_reason — dua sisi, semuanya ROLLBACK, nol baris menetap.
--
-- SISI MERAH sudah terukur di data NYATA sebelum perubahan:
--   16 dari 16 bank_transactions ber-status VOIDED punya voided_by NULL DAN
--   void_reason NULL. Tak satu pun void transaksi bank di sistem ini pernah
--   mencatat SIAPA dan KENAPA. Itu keadaan lama; tak perlu disimulasikan.
--
-- BATAS: ini membuktikan PERNYATAAN SQL-nya, bukan jalur HTTP ujung-ke-ujung.

\set TENANT 'kaos-biru-konveksi'
SELECT set_config('app.tenant_id', :'TENANT', true);

\echo ''
\echo '════════ [1] SISI MERAH — keadaan NYATA hari ini ════════'
SELECT count(*) AS voided_total,
       count(voided_by)    AS ada_pelaku,
       count(void_reason)  AS ada_alasan
FROM bank_transactions WHERE status = 'VOIDED';

\echo ''
\echo '════════ [2] SISI HIJAU — pernyataan BARU atas baris NYATA (ROLLBACK) ════════'
BEGIN;

-- pilih satu baris VOIDED yang saat ini tanpa pelaku/alasan
CREATE TEMP TABLE sasaran AS
SELECT id, voided_by AS pelaku_sebelum, void_reason AS alasan_sebelum
FROM bank_transactions
WHERE status = 'VOIDED' AND voided_by IS NULL AND void_reason IS NULL
ORDER BY id LIMIT 1;

SELECT (SELECT count(*) FROM sasaran) AS baris_sasaran;

-- pernyataan PERSIS seperti yang dikirim ke produksi (empat kolom)
UPDATE bank_transactions
   SET status = 'VOIDED', voided_by = $$00000000-0000-0000-0000-0000000000ff$$::uuid,
       voided_at = NOW(), void_reason = $$uji gerbang (ROLLBACK)$$
 WHERE id = (SELECT id FROM sasaran);

SELECT CASE
    WHEN bt.voided_by IS NOT NULL AND bt.void_reason IS NOT NULL
        THEN '[H] HIJAU — pelaku DAN alasan terisi'
    WHEN bt.voided_by IS NULL
        THEN '[X] MERAH: voided_by masih NULL'
    ELSE '[X] MERAH: void_reason masih NULL'
END AS hasil_hijau
FROM bank_transactions bt WHERE bt.id = (SELECT id FROM sasaran);

-- sabotase: jatuhkan void_reason -> assertion WAJIB memerah
UPDATE bank_transactions SET void_reason = NULL
 WHERE id = (SELECT id FROM sasaran);

SELECT CASE
    WHEN bt.voided_by IS NOT NULL AND bt.void_reason IS NOT NULL
        THEN '[X] GERBANG TAK SAH: sabotase LOLOS'
    ELSE '[M] sabotase memerah dgn benar — assertion ini bisa gagal'
END AS uji_sabotase
FROM bank_transactions bt WHERE bt.id = (SELECT id FROM sasaran);

ROLLBACK;

\echo ''
\echo '════════ [3] KONTROL — nol perubahan menetap ════════'
SELECT count(*) AS voided_total,
       count(voided_by) AS ada_pelaku,
       count(void_reason) AS ada_alasan,
       count(*) FILTER (WHERE void_reason = 'uji gerbang (ROLLBACK)') AS sisa_uji
FROM bank_transactions WHERE status = 'VOIDED';
