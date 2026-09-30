-- Rollback V334: cabut flag + indeks + fungsi + urutan + kolom nomor kasbon (data kasbon lain tak tersentuh).
DELETE FROM tenant_features WHERE tenant_id = 'kaos-biru-konveksi' AND feature = 'conversational_kasbon_page';
DROP INDEX IF EXISTS uq_employee_advances_tenant_number;
DROP FUNCTION IF EXISTS generate_employee_advance_number(text);
DROP TABLE IF EXISTS employee_advance_sequences;
ALTER TABLE employee_advances DROP COLUMN IF EXISTS advance_number;
