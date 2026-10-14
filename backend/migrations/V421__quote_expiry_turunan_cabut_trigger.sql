-- V421 (14 Okt 2026, BACKEND; W-20261014-QUOTE-EXPIRY-TURUNAN, kontrak disetujui MASTER) -- "kedaluwarsa" penawaran
-- = TURUNAN tanggal (expiry_date < tanggal bisnis tenant; services/penawaran_kedaluwarsa), BUKAN status tersimpan.
-- trg_quote_expiry (V039, V306) membalik sent -> 'expired' pada UPDATE APA PUN yang mempertahankan 'sent' -- dua sumber
-- kebenaran: jalur ubah/perpanjang harus memulihkannya, dan /send ulang pada penawaran lewat masa MENDAMPARKANNYA
-- (terima/tolak/batal/konversi menolak 'expired'). Pola NetSuite/Xero: kedaluwarsa dihitung dari tanggal.
-- Ukur 14 Okt (READ ONLY): status='expired' = 0 baris di SEMUA tenant -> nol baris berubah, nol backfill.
-- CHECK chk_quotes_status TIDAK diubah ('expired' tetap nilai sah; tak ada lagi penulisnya -- dijaga tes unit).
-- Tanpa efek uang. URUTAN WAJIB: migrasi ini DULU, baru kode (kode baru membuang pemulih 'expired'->'sent'; kode baru
-- + trigger hidup = ubah penawaran terkirim lewat masa -> TERDAMPAR 'expired'). Kode lama tanpa trigger = aman.
DROP TRIGGER IF EXISTS trg_quote_expiry ON quotes;
DROP FUNCTION IF EXISTS check_quote_expiry();
