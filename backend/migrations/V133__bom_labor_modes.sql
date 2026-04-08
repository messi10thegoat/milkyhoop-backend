-- V133: BOM labor modes — extend V132 subcontracting with flexible labor modes
-- Modes: time_based (default), piece_rate (internal borongan), subcontract (V132 flow), none

ALTER TABLE bom_operations
  ADD COLUMN labor_mode TEXT NOT NULL DEFAULT 'time_based'
    CHECK (labor_mode IN ('time_based','piece_rate','subcontract','none')),
  ADD COLUMN cost_per_piece NUMERIC(18,2);

UPDATE bom_operations SET labor_mode = 'subcontract' WHERE is_subcontract = TRUE;

CREATE OR REPLACE FUNCTION sync_bom_op_labor_mode() RETURNS trigger AS $$
BEGIN
  IF NEW.is_subcontract = TRUE AND NEW.labor_mode = 'time_based' THEN
    NEW.labor_mode := 'subcontract';
  END IF;
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
