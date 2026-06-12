-- V175__doc_intake_lane_c.sql
-- Phase 1a "Seize Turn Ownership + Lane C".
-- Additive & idempotent: nullable columns + widened CHECK. No data backfill,
-- no behavior change on its own. All new behavior is gated by env DOC_INTAKE_LANE_C=on.

-- 1) documents: real intake disposition (replaces silent hardcoded category='receipt').
ALTER TABLE documents
    ADD COLUMN IF NOT EXISTS intake_outcome TEXT NULL;

COMMENT ON COLUMN documents.intake_outcome IS
    'Lane C / intake disposition: posted | routed_crud | '
    'lane_c_captured_non_transaction | lane_c_captured_unsupported_txtype | '
    'lane_c_captured_unsupported_filetype | NULL (legacy / pre-Lane-C).';

-- 2) document_intake_log (partitioned parent → cascades to partitions): terminal outcome.
ALTER TABLE document_intake_log
    ADD COLUMN IF NOT EXISTS final_outcome TEXT NULL;

ALTER TABLE document_intake_log
    DROP CONSTRAINT IF EXISTS chk_intake_final_outcome;
ALTER TABLE document_intake_log
    ADD CONSTRAINT chk_intake_final_outcome CHECK (
        final_outcome IS NULL OR final_outcome IN (
            'posted', 'clarified', 'lane_c_captured',
            'routed_crud', 'generic_chat', 'abandoned'
        )
    );

COMMENT ON COLUMN document_intake_log.final_outcome IS
    'Terminal disposition of the file-bearing turn. Leak metric: '
    'count(final_outcome=''generic_chat'') over file-turns MUST be 0 when Lane C is on.';

-- 3) document_attachments: allow anti-orphan link to the chat message that produced
--    a Lane C capture (entity_type=''chat_message'', entity_id=message_id).
ALTER TABLE document_attachments
    DROP CONSTRAINT IF EXISTS chk_da_entity;
ALTER TABLE document_attachments
    ADD CONSTRAINT chk_da_entity CHECK (
        (entity_type)::text = ANY (ARRAY[
            'sales_invoice','bill','expense','customer','vendor','item','journal',
            'quote','purchase_order','sales_order','sales_receipt','payment',
            'credit_note','vendor_credit','stock_adjustment','stock_transfer',
            'employee','asset','project','contract','chat_message','other'
        ]::text[])
    );
