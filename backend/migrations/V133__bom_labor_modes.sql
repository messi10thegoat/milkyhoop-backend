-- V133: BOM labor modes — extend V132 subcontracting with flexible labor modes
-- Modes: time_based (default), piece_rate (internal borongan), subcontract (V132 flow), none
--
-- Backward compatible with V132: existing is_subcontract=true rows backfill to 'subcontract'.
-- Subcontract flow (auto-Bill, production_subcontracts) keeps using existing columns.

ALTER TABLE bom_operations
  ADD COLUMN labor_mode TEXT NOT NULL DEFAULT 'time_based'
    CHECK (labor_mode IN ('time_based','piece_rate','subcontract','none')),
  ADD COLUMN cost_per_piece NUMERIC(18,2);

-- Backfill existing subcontract rows
UPDATE bom_operations SET labor_mode = 'subcontract' WHERE is_subcontract = TRUE;

-- Sanity: any row flagged is_subcontract must now be labor_mode='subcontract' and vice versa.
-- Enforced via trigger so V132 subcontracting code (which only sets is_subcontract) stays valid:
CREATE OR REPLACE FUNCTION sync_bom_op_labor_mode() RETURNS trigger AS $$
BEGIN
  -- If caller set is_subcontract without labor_mode, promote to subcontract.
  IF NEW.is_subcontract = TRUE AND NEW.labor_mode = 'time_based' THEN
    NEW.labor_mode := 'subcontract';
  END IF;
  -- If labor_mode='subcontract', force is_subcontract=true for V132 compat.
  IF NEW.labor_mode = 'subcontract' THEN
    NEW.is_subcontract := TRUE;
  ELSIF NEW.labor_mode IN ('time_based','piece_rate','none') THEN
    NEW.is_subcontract := FALSE;
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_sync_bom_op_labor_mode ON bom_operations;
CREATE TRIGGER trg_sync_bom_op_labor_mode
  BEFORE INSERT OR UPDATE ON bom_operations
  FOR EACH ROW EXECUTE FUNCTION sync_bom_op_labor_mode();
