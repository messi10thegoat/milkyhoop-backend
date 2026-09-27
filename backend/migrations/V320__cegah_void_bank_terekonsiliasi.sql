-- V320 (27 Sep 2026, tiket FE Law 34) — void dokumen yang mutasi banknya SUDAH DIREKONSILIASI/DICOCOKKAN = DITOLAK.
-- Pola Xero/QBO: transaksi terekonsiliasi harus dilepas dulu. Chokepoint: SEMUA void (22 berkas router/servis)
-- menandai jurnal asli reversed_by_id (Law 2; terukur: 0 pembalik tanpa penanda asli) -> satu trigger menjaga
-- semua jalur, termasuk yang tak punya pemeriksaan di kode. Keadaan: bank_transactions.is_reconciled (sesi selesai)
-- / matched_statement_line_id (dicocokkan, sesi berjalan). Terukur 27 Sep: 0 baris di SEMUA tenant -> dorman.
-- Aditif: fungsi + trigger + indeks; nol perubahan data.

CREATE INDEX IF NOT EXISTS idx_bank_transactions_journal_id ON bank_transactions (journal_id);

CREATE OR REPLACE FUNCTION cegah_void_bank_terekonsiliasi() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    v_rekon int;
    v_cocok int;
BEGIN
    SELECT count(*) FILTER (WHERE COALESCE(bt.is_reconciled, false)),
           count(*) FILTER (WHERE NOT COALESCE(bt.is_reconciled, false) AND bt.matched_statement_line_id IS NOT NULL)
      INTO v_rekon, v_cocok
      FROM bank_transactions bt
     WHERE bt.journal_id = OLD.id AND bt.tenant_id = OLD.tenant_id;
    IF v_rekon > 0 THEN
        RAISE EXCEPTION 'BANK_TX_RECONCILED: jurnal % punya mutasi bank yang sudah direkonsiliasi; tak bisa dibalik', OLD.journal_number
            USING ERRCODE = 'check_violation';
    END IF;
    IF v_cocok > 0 THEN
        RAISE EXCEPTION 'BANK_TX_MATCHED: jurnal % punya mutasi bank yang sudah dicocokkan ke rekening koran; lepas cocok dulu', OLD.journal_number
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END $$;

DROP TRIGGER IF EXISTS trg_cegah_void_bank_terekonsiliasi ON journal_entries;
CREATE TRIGGER trg_cegah_void_bank_terekonsiliasi
    BEFORE UPDATE OF reversed_by_id ON journal_entries
    FOR EACH ROW
    WHEN (OLD.reversed_by_id IS NULL AND NEW.reversed_by_id IS NOT NULL)
    EXECUTE FUNCTION cegah_void_bank_terekonsiliasi();
