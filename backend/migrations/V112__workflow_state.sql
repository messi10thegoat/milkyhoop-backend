-- Workflow state machine: persistent state for deterministic workflow control
-- LLM = Interpreter (NLU + narration), Code = Controller (state transitions)

CREATE TABLE IF NOT EXISTS chat_workflow_state (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL,
    user_id UUID NOT NULL,
    chat_session_id TEXT NOT NULL,
    workflow_type TEXT NOT NULL DEFAULT 'bank_reconciliation',
    current_state TEXT NOT NULL DEFAULT 'IDENTIFY_ACCOUNT',
    status TEXT NOT NULL DEFAULT 'active',
    data JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(chat_session_id, workflow_type)
);

CREATE INDEX IF NOT EXISTS idx_workflow_state_session ON chat_workflow_state(chat_session_id);
CREATE INDEX IF NOT EXISTS idx_workflow_state_tenant ON chat_workflow_state(tenant_id, status);
