-- ============================================================================
-- V240: Rapikan dwi-status beban yang tersangkut di bawaan kolom
-- ============================================================================
-- SEBAB: V104 menambah operational_status/accounting_status dengan bawaan
-- 'DRAFT'/'UNPOSTED'. V105 mem-backfill baris yang SUDAH ADA saat itu. Tapi
-- jalur pembuatan beban TAK PERNAH mengadopsi operational_status -- nol penulis
-- di seluruh backend sampai 12 Sep 2026. Akibatnya SETIAP baris yang lahir
-- SESUDAH V105 tersangkut di 'DRAFT', termasuk yang sudah posted dan void.
--
-- MENJALANKAN ULANG V105 TIDAK MEMPERBAIKINYA: penyaringnya menuntut
-- accounting_status='UNPOSTED', sedangkan baris-baris ini ber-'POSTED'
-- (accounting_status MEMANG diadopsi jalur tulis; hanya operational_status yang
-- tidak). Nol baris akan cocok. Terukur 12 Sep.
--
-- URUTAN YANG DIPEGANG: sumber dulu, data kemudian. Jalur tulis diperbaiki dan
-- TERBIT lebih dulu (BE 4c6822d5 -- create/post/void kini menulis
-- DRAFT/PAID/VOID + UNPOSTED/POSTED/REVERSED). Migrasi ini merapikan sisanya.
-- Tanpa urutan itu, baris berikutnya tersangkut lagi seketika.
--
-- ⚠️ PENYARINGNYA SENGAJA BUKAN "semua yang DRAFT". Sejak 12 Sep draf
-- SUNGGUHAN bisa ada, dan baris status='draft' + operational_status='DRAFT'
-- adalah BENAR, bukan tersangkut. Penyaring di bawah membandingkan tiap baris
-- terhadap pemetaan V105 atas DIRINYA SENDIRI, jadi ia tak pernah menyentuh
-- baris yang sudah benar -- termasuk draf sungguhan, dan termasuk baris yang
-- ditulis jalur baru.
-- ============================================================================

-- Snapshot SEBELUM mengubah, supaya ROLLBACK bisa memulihkan TEPAT baris yang
-- disentuh. Tanpa ini, rollback "balik-petakan" akan ikut merusak baris yang
-- nilainya kebetulan sama tapi TAK PERNAH tersangkut (mis. yang ditulis jalur
-- baru) -- rollback yang merusak lebih buruk daripada tak ada rollback.
CREATE TABLE IF NOT EXISTS v240_snapshot_beban_dwi_status (
    expense_id UUID PRIMARY KEY,
    operational_status_lama VARCHAR(20),
    accounting_status_lama VARCHAR(20),
    diambil_pada TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

INSERT INTO v240_snapshot_beban_dwi_status
    (expense_id, operational_status_lama, accounting_status_lama)
SELECT id, operational_status, accounting_status
FROM expenses
WHERE operational_status IS DISTINCT FROM (
        CASE status
            WHEN 'draft'  THEN 'DRAFT'
            WHEN 'posted' THEN 'PAID'
            WHEN 'void'   THEN 'VOID'
            ELSE 'DRAFT'
        END)
   OR accounting_status IS DISTINCT FROM (
        CASE
            WHEN status = 'draft' THEN 'UNPOSTED'
            WHEN status = 'void' AND journal_id IS NOT NULL THEN 'REVERSED'
            WHEN journal_id IS NOT NULL THEN 'POSTED'
            ELSE 'UNPOSTED'
        END)
ON CONFLICT (expense_id) DO NOTHING;

UPDATE expenses SET
    operational_status = CASE status
        WHEN 'draft'  THEN 'DRAFT'
        WHEN 'posted' THEN 'PAID'
        WHEN 'void'   THEN 'VOID'
        ELSE 'DRAFT'
    END,
    accounting_status = CASE
        WHEN status = 'draft' THEN 'UNPOSTED'
        WHEN status = 'void' AND journal_id IS NOT NULL THEN 'REVERSED'
        WHEN journal_id IS NOT NULL THEN 'POSTED'
        ELSE 'UNPOSTED'
    END,
    updated_at = NOW()
WHERE operational_status IS DISTINCT FROM (
        CASE status
            WHEN 'draft'  THEN 'DRAFT'
            WHEN 'posted' THEN 'PAID'
            WHEN 'void'   THEN 'VOID'
            ELSE 'DRAFT'
        END)
   OR accounting_status IS DISTINCT FROM (
        CASE
            WHEN status = 'draft' THEN 'UNPOSTED'
            WHEN status = 'void' AND journal_id IS NOT NULL THEN 'REVERSED'
            WHEN journal_id IS NOT NULL THEN 'POSTED'
            ELSE 'UNPOSTED'
        END);

-- Nol baris tersisa yang tak sepakat dengan pemetaannya sendiri.
-- Kalau ini gagal, JANGAN commit: ada nilai status di luar draft/posted/void.
DO $$
DECLARE sisa INT;
BEGIN
    SELECT count(*) INTO sisa FROM expenses
    WHERE operational_status IS DISTINCT FROM (
            CASE status WHEN 'draft' THEN 'DRAFT' WHEN 'posted' THEN 'PAID'
                        WHEN 'void' THEN 'VOID' ELSE 'DRAFT' END)
       OR accounting_status IS DISTINCT FROM (
            CASE WHEN status = 'draft' THEN 'UNPOSTED'
                 WHEN status = 'void' AND journal_id IS NOT NULL THEN 'REVERSED'
                 WHEN journal_id IS NOT NULL THEN 'POSTED' ELSE 'UNPOSTED' END);
    IF sisa <> 0 THEN
        RAISE EXCEPTION 'V240 gagal: % baris masih tak sepakat dgn pemetaannya', sisa;
    END IF;
END $$;
