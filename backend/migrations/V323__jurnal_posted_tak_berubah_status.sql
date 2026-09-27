-- V323 (MASTER 28 Sep 2026; Law 2 + Law 22) — pagar DB untuk kelas cacat "rantai nomor ganda" (TEMUAN-rantai-nomor-ganda-20260912).
-- 1) prevent_posted_journal_update: status POSTED TIDAK BOLEH berubah ke apa pun. Dulu POSTED->VOID DIIZINKAN eksplisit
--    (dan POSTED->status lain lolos karena blok akhir hanya memeriksa total/tanggal/deskripsi). Void = jurnal pembalik +
--    reversed_by_id pada asli (Law 2). Terukur 28 Sep: nol penulis status VOID pada journal_entries (kode & fungsi DB),
--    nol jurnal VOID baru sejak 2026-09-12; 16 VOID lama di kaos = data sejarah (dikecualikan V241, tak disentuh).
-- 2) UNIQUE (tenant_id, chain_sequence) WHERE status='POSTED' — Law 22 kini dipagari DB (dulu btree biasa). Terukur:
--    0 duplikat di antara POSTED (9 duplikat lama semuanya VOID-vs-POSTED, di luar predikat).
-- Rollback: V323_ROLLBACK.

CREATE OR REPLACE FUNCTION public.prevent_posted_journal_update()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
BEGIN
    IF OLD.status = 'POSTED' THEN
        -- Law 2: jurnal POSTED tak pernah berganti status (void = pembalik + reversed_by_id).
        IF NEW.status IS DISTINCT FROM 'POSTED' THEN
            RAISE EXCEPTION 'Law 2: status jurnal POSTED % tidak boleh berubah menjadi %. Batalkan lewat jurnal pembalik (reversed_by_id).',
                OLD.journal_number, NEW.status
                USING ERRCODE = 'check_violation';
        END IF;

        -- Allow reversed_by_id and reversed_at updates (for reversal linking)
        IF (NEW.reversed_by_id IS DISTINCT FROM OLD.reversed_by_id
            OR NEW.reversed_at IS DISTINCT FROM OLD.reversed_at) THEN
            -- Only allow if other critical fields unchanged
            IF NEW.total_debit = OLD.total_debit
               AND NEW.total_credit = OLD.total_credit
               AND NEW.journal_date = OLD.journal_date
               AND NEW.description = OLD.description
               AND NEW.status = OLD.status THEN
                RETURN NEW;
            END IF;
        END IF;

        -- Block all other modifications to POSTED journals
        IF NEW.total_debit != OLD.total_debit
           OR NEW.total_credit != OLD.total_credit
           OR NEW.journal_date != OLD.journal_date
           OR NEW.description != OLD.description THEN
            RAISE EXCEPTION 'Cannot modify POSTED journal entry (%). Use reversal instead.', OLD.journal_number;
        END IF;
    END IF;

    RETURN NEW;
END;
$function$;

CREATE UNIQUE INDEX IF NOT EXISTS uq_je_chain_seq_posted
    ON journal_entries (tenant_id, chain_sequence) WHERE status = 'POSTED';
