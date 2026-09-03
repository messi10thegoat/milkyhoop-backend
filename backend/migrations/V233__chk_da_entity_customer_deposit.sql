-- V233: izinkan entity_type = 'customer_deposit' di document_attachments.
--
-- KENAPA: modul Uang Muka Pelanggan (customer_deposits) belum punya rute
-- lampiran sama sekali, dan `document_attachments` — satu-satunya tabel
-- lampiran generik — menolak entity_type baru lewat CHECK `chk_da_entity`.
-- Tanpa migrasi ini, INSERT lampiran deposit gagal keras:
--     ERROR: new row for relation "document_attachments" violates check
--            constraint "chk_da_entity"
-- (Diukur 2026-09-03 dengan INSERT percobaan di dalam BEGIN/ROLLBACK.)
--
-- KENAPA TABEL GENERIK, BUKAN TABEL KHUSUS:
-- Faktur penjualan memakai tabel warisan `sales_invoice_attachments`, dan
-- endpoint fakturnya kini harus meng-UNION dua tabel supaya lampiran dari
-- chat (yang mendarat di `document_attachments`) ikut terlihat. Menambah satu
-- lagi tabel khusus berarti menambah satu lagi UNION untuk setiap konsumen
-- di masa depan. Modul BARU memakai jalur modern saja — pola yang sama
-- dipakai `expenses`.
--
-- DAFTAR NILAI DISALIN VERBATIM dari `pg_get_constraintdef(oid)` pada
-- 2026-09-03 (22 nilai), lalu DITAMBAH satu: 'customer_deposit'. Daftar ini
-- TIDAK diketik ulang dari ingatan atau dari migrasi lama — sumber sahihnya
-- adalah constraint yang benar-benar hidup di basis data.
--
-- HURUF KECIL, dan itu disengaja: seluruh 22 nilai yang ada memakai
-- snake_case huruf kecil, dan kedua nilai yang BENAR-BENAR tersimpan hari ini
-- ('payment' 5 baris, 'sales_invoice' 1 baris) juga huruf kecil.
--
-- CATATAN CACAT TERPISAH (tidak diperbaiki di sini, bukan lingkup tiket ini):
-- `routers/expenses.py` menyisipkan entity_type 'EXPENSE' HURUF BESAR di 1
-- situs dan membacanya kembali di 2 situs. Huruf besar itu DITOLAK CHECK ini
-- (sebelum maupun sesudah V233), sehingga jalur "buat pengeluaran dengan
-- attachment_ids" pasti gagal. Buktinya: nol baris 'EXPENSE' di tabel.
-- Migrasi ini SENGAJA tidak menambahkan 'EXPENSE' — menambahkannya akan
-- membekukan ketidakkonsistenan huruf; perbaikan yang benar adalah mengubah
-- kode expenses ke huruf kecil. Dilaporkan sebagai tiket tersendiri.

ALTER TABLE document_attachments DROP CONSTRAINT IF EXISTS chk_da_entity;

ALTER TABLE document_attachments ADD CONSTRAINT chk_da_entity CHECK (
    (entity_type)::text = ANY (ARRAY[
        'sales_invoice'::text,
        'bill'::text,
        'expense'::text,
        'customer'::text,
        'vendor'::text,
        'item'::text,
        'journal'::text,
        'quote'::text,
        'purchase_order'::text,
        'sales_order'::text,
        'sales_receipt'::text,
        'payment'::text,
        'credit_note'::text,
        'vendor_credit'::text,
        'stock_adjustment'::text,
        'stock_transfer'::text,
        'employee'::text,
        'asset'::text,
        'project'::text,
        'contract'::text,
        'chat_message'::text,
        'other'::text,
        'customer_deposit'::text          -- BARU di V233
    ])
);
