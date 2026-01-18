-- ============================================================================
-- V076: Fix Race Condition in Bank Account Balance Calculation
-- ============================================================================
-- Problem: Current trigger uses NEW.running_balance from INSERT, causing
--          race condition on concurrent payments.
--
-- Solution: Trigger now calculates balance atomically using UPDATE...RETURNING
--           instead of relying on INSERT value.
--
-- Scenario Fixed:
-- Time    Transaction A              Transaction B
-- ────────────────────────────────────────────────────
-- T1      SELECT balance → 1,000,000
-- T2                                 SELECT balance → 1,000,000
-- T3      Calculate: 1M - 500K = 500K
-- T4                                 Calculate: 1M - 300K = 700K
-- T5      INSERT running_balance = 500K
-- T6      Trigger: balance = 500K ✓ (ATOMIC UPDATE)
-- T7                                 INSERT running_balance = 700K
-- T8                                 Trigger: balance = 200K ✓ (ATOMIC UPDATE)
-- ============================================================================

-- ============================================================================
-- 1. UPDATE TRIGGER FUNCTION FOR ATOMIC BALANCE CALCULATION
-- ============================================================================

CREATE OR REPLACE FUNCTION update_bank_account_balance()
RETURNS TRIGGER AS $$
DECLARE
    v_new_balance NUMERIC(18,2);
BEGIN
    -- Atomic update: current_balance + amount (no race condition)
    -- The UPDATE...RETURNING ensures we get the actual new balance
    -- even when multiple transactions execute concurrently
    UPDATE bank_accounts
    SET current_balance = current_balance + NEW.amount,
        updated_at = NOW()
    WHERE id = NEW.bank_account_id
    RETURNING current_balance INTO v_new_balance;

    -- Update running_balance in transaction record for audit trail
    -- This is now calculated atomically by the database
    NEW.running_balance := v_new_balance;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION update_bank_account_balance IS
    'Atomic balance update trigger - prevents race conditions on concurrent payments';

-- ============================================================================
-- 2. ENSURE TRIGGER IS BEFORE INSERT (to modify NEW.running_balance)
-- ============================================================================

-- Drop existing trigger (was AFTER INSERT)
DROP TRIGGER IF EXISTS trg_update_bank_balance ON bank_transactions;

-- Recreate as BEFORE INSERT so we can modify NEW.running_balance
CREATE TRIGGER trg_update_bank_balance
    BEFORE INSERT ON bank_transactions
    FOR EACH ROW
    EXECUTE FUNCTION update_bank_account_balance();

-- ============================================================================
-- 3. DEPRECATE calculate_bank_running_balance FUNCTION
-- ============================================================================
-- This function is no longer needed - balance is calculated atomically in trigger
-- Keeping it for backward compatibility but adding deprecation notice

COMMENT ON FUNCTION calculate_bank_running_balance IS
    'DEPRECATED: Running balance is now calculated atomically in trigger. Use 0 for running_balance in INSERT.';

-- ============================================================================
-- MIGRATION COMPLETE
-- ============================================================================

DO $$
BEGIN
    RAISE NOTICE 'Migration V076: Bank Balance Trigger Fix completed successfully';
    RAISE NOTICE '- Trigger now BEFORE INSERT (was AFTER INSERT)';
    RAISE NOTICE '- Balance calculated atomically via UPDATE...RETURNING';
    RAISE NOTICE '- Race condition on concurrent payments is now fixed';
    RAISE NOTICE '';
    RAISE NOTICE 'Application code update required:';
    RAISE NOTICE '- Remove running_balance calculation before INSERT';
    RAISE NOTICE '- Just pass the amount, trigger handles balance';
END $$;
