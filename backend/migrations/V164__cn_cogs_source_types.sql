-- V164: Register journal_source_types for CN companion COGS reversal (Fase 4)
-- Preemptive insertion to avoid FK violation (lesson: payroll Surprise #7).
--
-- CREDIT_NOTE_COGS    : Sales credit note inventory return COGS reversal companion journal
--                       (mirror of VENDOR_CREDIT_COGS on purchase side)
-- RECLASSIFY_CN_COGS_GAP : Retroactive adjustment for pre-fix CN where companion
--                          journal was never emitted (Option A one-shot reclassification).

INSERT INTO journal_source_types (source_type, description) VALUES
  ('CREDIT_NOTE_COGS',
   'Customer credit note inventory return COGS reversal companion (Dr Inventory / Cr COGS at WAC)'),
  ('RECLASSIFY_CN_COGS_GAP',
   'Retroactive CN COGS gap reclassification (pre-V164 fix one-shot adjustment)')
ON CONFLICT (source_type) DO NOTHING;
