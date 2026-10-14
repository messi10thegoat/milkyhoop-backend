-- V427 (14 Okt 2026, BACKEND2; W-20261014-JUMLAH-BARIS, Opsi A disetujui MASTER) -- JUMLAH BARIS yang diketik pengguna
-- (NetSuite Amount<->Rate, Accurate "Jumlah"). Iron Law 9 (Decimal HALF_UP 2dp), Law 1/16 (faktur == SO).
-- Bila terisi, jumlah ini MENGGANTIKAN q2(qty x harga) pada tingkat BRUTO (sebelum diskon baris); kalkulator
-- (services/sales_doc_calc.line_net) memvalidasi |qty x harga - jumlah| <= qty x 0,005 saat DIKETIK, kolom tersimpan
-- dipercaya. Pada sales_invoice_items = jumlah kotor baris faktur (pro-rata dari SO; faktur yang menuntaskan baris
-- menyerap sisa). quote_items: kolom disiapkan agar Penawaran->SO kelak membawanya (kode Penawaran unit terpisah).
-- NULL = perilaku lama persis. Tanpa backfill. Urutan: migrasi DULU, baru kode.
-- Kode lama + kolom baru = aman (kolom NULL tak dibaca).
ALTER TABLE sales_order_items ADD COLUMN IF NOT EXISTS jumlah_baris NUMERIC(18,2) NULL;
ALTER TABLE sales_invoice_items ADD COLUMN IF NOT EXISTS jumlah_baris NUMERIC(18,2) NULL;
ALTER TABLE quote_items ADD COLUMN IF NOT EXISTS jumlah_baris NUMERIC(18,2) NULL;
-- credit_note_items: NK atas baris faktur berjumlah-diketik membawa jumlah (pro-rata; penuntas = sisa) agar NK penuh
-- tidak meninggalkan selisih pembulatan di piutang (Law 1/16).
ALTER TABLE credit_note_items ADD COLUMN IF NOT EXISTS jumlah_baris NUMERIC(18,2) NULL;
