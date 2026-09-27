-- Rollback V323: fungsi kembali PERSIS seperti sebelum V323 (disalin dari prod 28 Sep) + indeks unik dilepas.
CREATE OR REPLACE FUNCTION public.prevent_posted_journal_update()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
BEGIN
    -- Only apply checks if original status was POSTED
    IF OLD.status = 'POSTED' THEN
        -- Allow legitimate void flow: POSTED → VOID
        IF NEW.status = 'VOID' AND OLD.status = 'POSTED' THEN
            RETURN NEW;
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

        -- Allow voided_by and void_reason updates (during void process)
        IF (NEW.voided_by IS DISTINCT FROM OLD.voided_by
            OR NEW.void_reason IS DISTINCT FROM OLD.void_reason)
           AND NEW.status = 'VOID' THEN
            RETURN NEW;
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
$function$
;
DROP INDEX IF EXISTS uq_je_chain_seq_posted;
