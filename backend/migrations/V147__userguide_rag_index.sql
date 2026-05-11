-- V147__userguide_rag_index.sql
-- Phase 2A foundation for userguide RAG.
-- Creates pgvector extension, userguide_chunks (global, public-read),
-- userguide_query_log (per-tenant), and tenant_config.userguide_rag_enabled flag.
-- See: frontend/web/DOCS/userguide/_build/PHASE2_RAG_BRAINSTORM.md

CREATE EXTENSION IF NOT EXISTS vector;

-- ---------------------------------------------------------------
-- Table: userguide_chunks (single global index, public read)
-- Permission filter applied at query time via SQL parameters.
-- No RLS — userguide content is the same for all tenants.
-- ---------------------------------------------------------------
CREATE TABLE IF NOT EXISTS userguide_chunks (
    chunk_id        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    doc_id          VARCHAR(255) NOT NULL,
    doc_title       VARCHAR(500) NOT NULL,
    doc_path        VARCHAR(500) NOT NULL,
    module          VARCHAR(100) NOT NULL,
    type            VARCHAR(50)  NOT NULL,    -- konsep, how-to, troubleshooting, faq, glossary
    tier            VARCHAR(20)  NOT NULL,    -- plain, bridged, deep
    section_heading VARCHAR(500),
    section_level   SMALLINT,                 -- H2=2, H3=3
    chunk_index     INTEGER      NOT NULL,
    content         TEXT         NOT NULL,
    content_tokens  INTEGER      NOT NULL,
    content_hash    TEXT         NOT NULL,
    embedding       vector(1536) NOT NULL,
    required_module VARCHAR(50),
    required_action CHAR(1),
    related_ids     TEXT[],
    last_updated    DATE         NOT NULL,
    indexed_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),

    CONSTRAINT chk_userguide_tier CHECK (tier IN ('plain', 'bridged', 'deep')),
    CONSTRAINT chk_userguide_action CHECK (
        required_action IS NULL OR required_action IN ('C','R','U','D','V','A','P','E')
    ),
    CONSTRAINT uq_userguide_doc_chunk UNIQUE (doc_id, chunk_index)
);

-- HNSW index for cosine similarity search
CREATE INDEX IF NOT EXISTS idx_userguide_chunks_embedding
    ON userguide_chunks
    USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

CREATE INDEX IF NOT EXISTS idx_userguide_chunks_permission
    ON userguide_chunks (required_module, required_action);

CREATE INDEX IF NOT EXISTS idx_userguide_chunks_doc
    ON userguide_chunks (doc_id);

CREATE INDEX IF NOT EXISTS idx_userguide_chunks_module_tier
    ON userguide_chunks (module, tier);

-- ---------------------------------------------------------------
-- Table: userguide_query_log (cost monitoring + threshold tuning)
-- ---------------------------------------------------------------
CREATE TABLE IF NOT EXISTS userguide_query_log (
    log_id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       VARCHAR(255) NOT NULL,
    user_id         VARCHAR(255) NOT NULL,
    query_text      TEXT         NOT NULL,
    query_tokens    INTEGER      NOT NULL,
    chunks_returned INTEGER      NOT NULL,
    top_similarity  FLOAT,
    tier_used       VARCHAR(20),              -- 'tier_1' .. 'tier_4' or 'permission_gated'
    cost_usd        NUMERIC(10,6),
    response_ms     INTEGER,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_userguide_query_log_tenant_time
    ON userguide_query_log (tenant_id, created_at DESC);

-- ---------------------------------------------------------------
-- Feature flag: per-tenant rollout via tenant_config
-- ---------------------------------------------------------------
ALTER TABLE tenant_config
    ADD COLUMN IF NOT EXISTS userguide_rag_enabled BOOLEAN NOT NULL DEFAULT false;
