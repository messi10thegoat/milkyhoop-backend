-- V235: bill tidak boleh menjadi 'void' selagi jurnalnya masih POSTED dan
-- belum dibalik.
--
-- LATAR TERUKUR 2026-09-03: `BILL-2609-0002` berstatus `status_v2='void'`
-- sementara jurnalnya `PJ-2609-0002` masih POSTED tanpa `reversed_by_id`.
-- Akibatnya debit WIP 100.000 dari faktur yang sudah dibatalkan tetap berdiri,
-- dan kebetulan ia MENUTUPI kelebihan kredit di output WO-2026-000001 —
-- dua kesalahan yang saling meniadakan, sehingga tak satu pun terlihat.
--
-- KENAPA TRIGGER, BUKAN TAMBALAN DI KODE: seluruh kode aplikasi SUDAH benar.
-- Diukur: satu-satunya penulis `status_v2='void'` adalah `void_bill`
-- (`bills_service.py:2210`), dan ia membalik jurnal LEBIH DULU
-- (`reversed_by_id` diisi) baru menyetel status, dalam SATU transaksi
-- (Law 23). Baris cacat itu lahir dari `UPDATE` ad-hoc di luar aplikasi —
-- sidik jarinya jelas: `status='paid'` berdampingan dengan
-- `status_v2='void'`, `voided_at` NULL, `voided_reason` NULL, kombinasi yang
-- TIDAK BISA dihasilkan `void_bill` (ia menyetel keduanya sekaligus).
-- Menambal kode karena itu tak menutup apa pun; yang bisa menutup jalur SQL
-- langsung hanyalah invarian di basis data.
--
-- SIFAT: gagal-keras (RAISE EXCEPTION), sejalan dengan trigger Iron Law lain.
-- Ia TIDAK menyentuh, menonaktifkan, atau menggantikan trigger mana pun.
--
-- LINGKUP SEMPIT — hanya menolak bila SEMUA syarat ini benar sekaligus:
--   1. status_v2 BERUBAH menjadi 'void' (bukan update lain pada baris void),
--   2. ADA journal_entries source_type='BILL' source_id=bill.id,
--   3. jurnal itu status='POSTED',
--   4. DAN `reversed_by_id IS NULL`.
-- Sehingga: bill draft tanpa jurnal tetap boleh di-void; bill yang jurnalnya
-- SUDAH dibalik tetap boleh; dan jalur sah `void_bill` lolos karena ia sudah
-- mengisi `reversed_by_id` sebelum menyentuh status.

CREATE OR REPLACE FUNCTION trg_guard_void_bill_jurnal()
RETURNS TRIGGER AS $$
DECLARE
    v_jurnal TEXT;
BEGIN
    -- Hanya periksa saat status_v2 BERUBAH menjadi 'void'.
    IF NEW.status_v2 IS DISTINCT FROM 'void'
       OR OLD.status_v2 IS NOT DISTINCT FROM NEW.status_v2 THEN
        RETURN NEW;
    END IF;

    SELECT je.journal_number INTO v_jurnal
    FROM journal_entries je
    WHERE je.source_id = NEW.id
      AND je.source_type = 'BILL'
      AND je.status = 'POSTED'
      AND je.reversed_by_id IS NULL
    LIMIT 1;

    IF v_jurnal IS NOT NULL THEN
        RAISE EXCEPTION
            'Bill journal must be reversed before void (jurnal % masih POSTED tanpa pembalik, bill %)',
            v_jurnal, COALESCE(NEW.invoice_number, NEW.id::text)
            USING ERRCODE = 'raise_exception';
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_bills_guard_void_jurnal ON bills;

CREATE TRIGGER trg_bills_guard_void_jurnal
    BEFORE UPDATE OF status_v2 ON bills
    FOR EACH ROW
    EXECUTE FUNCTION trg_guard_void_bill_jurnal();

COMMENT ON FUNCTION trg_guard_void_bill_jurnal() IS
    'V235: menolak transisi bill ke status_v2=void selama jurnal BILL-nya masih POSTED dan belum dibalik. Menutup jalur UPDATE ad-hoc di luar aplikasi.';
