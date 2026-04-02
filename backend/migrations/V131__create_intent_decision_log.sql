-- Intent Decision Log — Observability for LLM-first classification
-- Logs every classification decision for analysis, ALG mining, and cost tracking

CREATE TABLE intent_decision_log (
    id BIGSERIAL,
    ts TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    tenant_id VARCHAR(255) NOT NULL,
    conversation_id UUID,
    session_id UUID,

    -- Input
    user_text TEXT NOT NULL,
    user_text_length INT,

    -- Gemini extraction result
    gemini_intent VARCHAR(100),
    gemini_confidence FLOAT,
    gemini_entities JSONB,
    gemini_latency_ms INT,

    -- Guard decisions (tracks ALL 6 guards)
    guard_triggered VARCHAR(50) DEFAULT 'none',
    guard_from VARCHAR(100),
    guard_to VARCHAR(100),
    guard_conflict BOOLEAN DEFAULT FALSE,
    guard_conflict_detail JSONB,

    -- Final decision
    final_intent VARCHAR(100) NOT NULL,
    final_confidence FLOAT,
    decision_source VARCHAR(50),

    -- Context
    context_hint_used BOOLEAN DEFAULT FALSE,
    last_action_type VARCHAR(100),

    -- Routing
    pipeline_or_agent VARCHAR(20),
    model_used VARCHAR(50),
    total_latency_ms INT,

    -- Cost tracking
    estimated_cost_usd FLOAT DEFAULT 0,
    input_tokens INT DEFAULT 0,
    output_tokens INT DEFAULT 0,

    -- Response
    response_type VARCHAR(30),
    response_length INT,

    -- User feedback (updated async)
    user_feedback SMALLINT DEFAULT 0,
    is_correction BOOLEAN DEFAULT FALSE,
    feedback_ts TIMESTAMPTZ
) PARTITION BY RANGE (ts);

-- Monthly partitions (3 months ahead)
CREATE TABLE intent_decision_log_2026_04 PARTITION OF intent_decision_log
    FOR VALUES FROM ('2026-04-01') TO ('2026-05-01');
CREATE TABLE intent_decision_log_2026_05 PARTITION OF intent_decision_log
    FOR VALUES FROM ('2026-05-01') TO ('2026-06-01');
CREATE TABLE intent_decision_log_2026_06 PARTITION OF intent_decision_log
    FOR VALUES FROM ('2026-06-01') TO ('2026-07-01');

-- Indexes for analysis queries
CREATE INDEX idx_idl_tenant_ts ON intent_decision_log (tenant_id, ts DESC);
CREATE INDEX idx_idl_guard ON intent_decision_log (guard_triggered) WHERE guard_triggered != 'none';
CREATE INDEX idx_idl_intent_pair ON intent_decision_log (gemini_intent, final_intent);
CREATE INDEX idx_idl_source ON intent_decision_log (decision_source, ts DESC);
CREATE INDEX idx_idl_feedback ON intent_decision_log (user_feedback) WHERE user_feedback != 0;
CREATE INDEX idx_idl_conflict ON intent_decision_log (guard_conflict) WHERE guard_conflict = true;
CREATE INDEX idx_idl_cost ON intent_decision_log (tenant_id, estimated_cost_usd DESC);
