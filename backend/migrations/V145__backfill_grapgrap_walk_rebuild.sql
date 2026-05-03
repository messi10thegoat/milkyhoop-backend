-- ============================================================================
-- V145 BACKFILL — walk-rebuild grapgrap chain
-- ============================================================================
-- Ticket: HASH-CHAIN-PROD-VIOLATION-001
-- Applied to prod: 2026-05-02 (immediately after V145 schema migration)
--
-- Why walk-rebuild instead of fixing 7 bogus entries:
-- verify_chain_integrity walks the chain advancing v_prev_hash to each
-- entry's stored content_hash. Entry N's validity therefore depends on
-- N-1's stored content_hash. Rewriting only the 7 bogus hashes invalidates
-- every downstream entry whose stored hash was originally computed against
-- the OLD bogus value. Cascade rebuild from chain_sequence=1 onward is the
-- mathematically correct fix.
--
-- Scope: tenant grapgrap only (312 rows). Other tenants (anthonius-iwan,
-- milkytest) reported 0 broken pre-migration and are untouched.
--
-- Idempotent: re-running this script produces the same hashes (deterministic
-- compute_journal_hash). Safe to run again if needed.
--
-- prevent_posted_journal_update allows the UPDATE because it only protects
-- total_debit/total_credit/journal_date/description; hash columns pass.
-- ============================================================================

DO $$
DECLARE
    v_entry RECORD;
    v_prev_hash VARCHAR := 'GENESIS';
    v_new_hash VARCHAR;
    v_count INT := 0;
BEGIN
    PERFORM pg_advisory_xact_lock(hashtext('journal_chain:grapgrap'));
    FOR v_entry IN
        SELECT id, chain_sequence
        FROM journal_entries
        WHERE tenant_id = 'grapgrap' AND status = 'POSTED'
        ORDER BY chain_sequence ASC
    LOOP
        v_new_hash := compute_journal_hash(v_entry.id, v_prev_hash);
        UPDATE journal_entries
           SET previous_hash = v_prev_hash,
               content_hash = v_new_hash
         WHERE id = v_entry.id;
        v_prev_hash := v_new_hash;
        v_count := v_count + 1;
    END LOOP;
    RAISE NOTICE 'Rebuilt % chain entries for tenant grapgrap', v_count;
END $$;

SELECT 'broken_after_walk_rebuild' AS metric,
       COUNT(*) FILTER (WHERE NOT is_valid) AS value
FROM verify_chain_integrity('grapgrap');
