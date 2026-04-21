-- V141__document_intake_log.sql
-- Document Intake V3 telemetry log. PII-safe. Partitioned monthly by ts.
-- Rows written fire-and-forget from DocumentIntakePipelineV3.process().

BEGIN;

CREATE TABLE IF NOT EXISTS document_intake_log (
    id UUID NOT NULL DEFAULT gen_random_uuid(),
    ts TIMESTAMPTZ NOT NULL DEFAULT now(),
    tenant_id TEXT NOT NULL,
    user_id UUID,
    session_id UUID,
    message_id UUID,

    ocr_hash TEXT,
    doc_type TEXT,
    amount_bucket TEXT,
    has_counterparty BOOLEAN,
    has_caption BOOLEAN,

    classified_type TEXT,
    classified_confidence NUMERIC(4,3),
    alternatives JSONB,
    signals_fired JSONB,
    ambiguity_reason TEXT,

    handler_selected TEXT,
    handler_match_success BOOLEAN,
    handler_needed_clarification BOOLEAN,
    handler_clarification_type TEXT,

    action_key TEXT,
    action_resolved BOOLEAN,
    pending_action_id UUID,

    user_confirmed BOOLEAN,
    user_cancelled BOOLEAN,
    user_corrected_type TEXT,

    classify_latency_ms INT,
    handler_latency_ms INT,
    total_latency_ms INT,
    estimated_cost_usd NUMERIC(10,6),

    error_stage TEXT,
    error_message TEXT,

    PRIMARY KEY (id, ts)
) PARTITION BY RANGE (ts);

CREATE TABLE IF NOT EXISTS document_intake_log_2026_04
    PARTITION OF document_intake_log
    FOR VALUES FROM ('2026-04-01') TO ('2026-05-01');

CREATE TABLE IF NOT EXISTS document_intake_log_2026_05
    PARTITION OF document_intake_log
    FOR VALUES FROM ('2026-05-01') TO ('2026-06-01');

CREATE TABLE IF NOT EXISTS document_intake_log_2026_06
    PARTITION OF document_intake_log
    FOR VALUES FROM ('2026-06-01') TO ('2026-07-01');

CREATE INDEX IF NOT EXISTS idx_doc_intake_log_tenant_ts
    ON document_intake_log (tenant_id, ts DESC);
CREATE INDEX IF NOT EXISTS idx_doc_intake_log_classified_type
    ON document_intake_log (classified_type, ts DESC);
CREATE INDEX IF NOT EXISTS idx_doc_intake_log_ambiguity
    ON document_intake_log (ambiguity_reason, ts DESC) WHERE ambiguity_reason IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_doc_intake_log_pending_action
    ON document_intake_log (pending_action_id) WHERE pending_action_id IS NOT NULL;

ALTER TABLE document_intake_log ENABLE ROW LEVEL SECURITY;
ALTER TABLE document_intake_log FORCE ROW LEVEL SECURITY;

CREATE POLICY document_intake_log_tenant_isolation ON document_intake_log
    USING (tenant_id = current_setting('app.tenant_id', true));

GRANT SELECT, INSERT, UPDATE ON document_intake_log TO milkyadmin;

COMMIT;
