-- Rollback V239. Membuang kolom = membuang isinya; jalankan hanya kalau yakin
-- belum ada tenant yang mengisi alamat workshop / penandatangan / alamat bank.
ALTER TABLE "Tenant"
    DROP COLUMN IF EXISTS workshop_address,
    DROP COLUMN IF EXISTS signatory_name;
ALTER TABLE bank_accounts
    DROP COLUMN IF EXISTS bank_address;
