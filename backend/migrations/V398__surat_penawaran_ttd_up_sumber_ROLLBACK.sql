-- ROLLBACK V398 (kode yang membaca kolom ini harus dibalik DULU).
ALTER TABLE quotes DROP COLUMN IF EXISTS attention_name, DROP COLUMN IF EXISTS attention_title,
    DROP COLUMN IF EXISTS signer_user_id, DROP COLUMN IF EXISTS signer_name, DROP COLUMN IF EXISTS signer_title,
    DROP COLUMN IF EXISTS signer_phone, DROP COLUMN IF EXISTS signer_email, DROP COLUMN IF EXISTS field_sources;
ALTER TABLE accounting_settings DROP COLUMN IF EXISTS default_quote_notes, DROP COLUMN IF EXISTS default_quote_terms,
    DROP COLUMN IF EXISTS default_quote_signer_user_id, DROP COLUMN IF EXISTS default_quote_signer_title,
    DROP COLUMN IF EXISTS default_quote_signer_phone;
