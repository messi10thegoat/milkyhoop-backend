-- V136: Add LLM Router shadow columns to intent_decision_log
ALTER TABLE intent_decision_log
ADD COLUMN IF NOT EXISTS llm_router_intent VARCHAR(100),
ADD COLUMN IF NOT EXISTS llm_router_confidence FLOAT,
ADD COLUMN IF NOT EXISTS llm_router_ready BOOLEAN,
ADD COLUMN IF NOT EXISTS llm_router_entities JSONB,
ADD COLUMN IF NOT EXISTS llm_router_reasoning TEXT,
ADD COLUMN IF NOT EXISTS llm_router_latency_ms INTEGER,
ADD COLUMN IF NOT EXISTS llm_router_agree BOOLEAN;

COMMENT ON COLUMN intent_decision_log.llm_router_intent IS 'Shadow LLM Router intent (Phase 1)';
COMMENT ON COLUMN intent_decision_log.llm_router_agree IS 'True if LLM Router agrees with regex pipeline';
