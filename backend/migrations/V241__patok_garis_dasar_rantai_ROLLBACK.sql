-- ROLLBACK V241 — cabut patok garis-dasar rantai hash.
--
-- Sesudah ini Check 2 kembali berbunyi CRITICAL tiap pagi untuk 2 tautan pecah
-- yang sudah ada. Itu KEADAAN SEBELUM V241, bukan kerusakan baru.
--
-- NOL data jurnal disentuh oleh V241 maupun oleh rollback ini: yang dibuat
-- hanya satu tabel pengampunan + satu fungsi pembaca. Tautan pecah 109 & 393
-- tetap apa adanya, sebelum dan sesudah.

BEGIN;

DROP FUNCTION IF EXISTS verify_chain_integrity_all();
DROP TABLE IF EXISTS journal_chain_exemptions;

COMMIT;
