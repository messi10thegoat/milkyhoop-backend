-- P2.4: Opening Balance Guard Trigger (Law 28)
-- Prevents posting opening balance journals after operational transactions exist.
-- Allows: OPENING → REVERSAL → Re-OPENING flow.

CREATE OR REPLACE FUNCTION guard_opening_balance()
RETURNS TRIGGER AS $$
DECLARE
    has_truly_operational BOOLEAN;
BEGIN
    IF NEW.source_type IN ('OPENING', 'OPENING_BALANCE') AND NEW.status = 'POSTED' THEN
        SELECT EXISTS(
            SELECT 1 FROM journal_entries je
            WHERE je.tenant_id = NEW.tenant_id
                AND je.status = 'POSTED'
                AND je.source_type NOT IN (
                    'OPENING', 'OPENING_BALANCE',
                    'OPENING_BALANCE_REVERSAL'
                )
                -- Also exclude reversals OF opening balance journals
                AND NOT (
                    je.source_type = 'REVERSAL'
                    AND EXISTS(
                        SELECT 1 FROM journal_entries orig
                        WHERE orig.id = je.source_id
                        AND orig.source_type IN ('OPENING', 'OPENING_BALANCE')
                    )
                )
        ) INTO has_truly_operational;

        IF has_truly_operational THEN
            RAISE EXCEPTION
                'Cannot post opening balance: operational transactions already exist for tenant %. Reverse existing opening balance first, then re-post.',
                NEW.tenant_id;
        END IF;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_guard_opening_balance ON journal_entries;

CREATE TRIGGER trg_guard_opening_balance
    BEFORE INSERT OR UPDATE ON journal_entries
    FOR EACH ROW
    EXECUTE FUNCTION guard_opening_balance();
