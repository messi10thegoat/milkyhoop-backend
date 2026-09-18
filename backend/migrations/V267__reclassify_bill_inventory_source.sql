-- V267: register source_type for RECLASSIFY_BILL_INVENTORY (S14/S1). Law 6 FK.
INSERT INTO journal_source_types (source_type, description) VALUES
  ('RECLASSIFY_BILL_INVENTORY', 'Reklas debit Persediaan salah dari BILL non-inventori ke HPP (S14/S1)')
ON CONFLICT (source_type) DO NOTHING;
