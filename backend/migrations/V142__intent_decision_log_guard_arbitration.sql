-- V142: GuardArbiter observability column
-- Ref: docs/plans/2026-04-22-p2-guard-audit-matrix.md v1.0
-- Depends on: V131 (intent_decision_log created)
-- ADR P4 pending_clarification migration = V143 (bumped).
--
-- Shape of guard_arbitration JSONB:
-- {
--   "winner":           "ARAP_GUARD" | "LLM" | "REC" | "PENDING_CLAR" | "NO_GUARD" | ...,
--   "final_intent":     "calc_rank_customers_by_ar",
--   "final_confidence": 1.0,
--   "guard_matches":    {"ARAP_GUARD": "query_ar_outstanding", ...},
--   "policy_applied":   "always_win" | "confidence_aware" | ...,
--   "conflict":         false,
--   "llm_intent_original":     "query_vendor_ap",
--   "llm_confidence_original": 0.72
-- }

ALTER TABLE intent_decision_log
  ADD COLUMN IF NOT EXISTS guard_arbitration JSONB NULL;

COMMENT ON COLUMN intent_decision_log.guard_arbitration IS
  'GuardArbiter decision payload (Phase-B 2026-04-22). See guard_arbiter.py:ArbitrationDecision.';

CREATE INDEX IF NOT EXISTS idx_intent_decision_log_arbiter_winner
  ON intent_decision_log ((guard_arbitration->>'winner')) WHERE guard_arbitration IS NOT NULL;
