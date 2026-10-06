-- V398 (MASTER 6 Okt 2026; putusan pemilik: PDF Penawaran bergaya surat, acuan "Surat Penawaran Harga" Accurate).
-- Aditif. Penawaran: up. (attention_*), penanda tangan + kontak (signer_*), sumber per medan (field_sources:
-- {"<medan>": "default"|"manual"}; kosong = penawaran lama sebelum unit ini). SNAPSHOT (pola 30 Sep): dokumen
-- menyimpan nilainya sendiri; setelan diubah tak menyentuh dokumen lama.
-- Setelan perusahaan (accounting_settings): bawaan catatan khusus, S&K, penanda tangan (pengguna tenant) + jabatan + HP
-- (profil pengguna TAK punya HP -- diukur 6 Okt). Nama & email penanda tangan dari profil pengguna.
ALTER TABLE quotes
    ADD COLUMN IF NOT EXISTS attention_name TEXT,
    ADD COLUMN IF NOT EXISTS attention_title TEXT,
    ADD COLUMN IF NOT EXISTS signer_user_id UUID,
    ADD COLUMN IF NOT EXISTS signer_name TEXT,
    ADD COLUMN IF NOT EXISTS signer_title TEXT,
    ADD COLUMN IF NOT EXISTS signer_phone TEXT,
    ADD COLUMN IF NOT EXISTS signer_email TEXT,
    ADD COLUMN IF NOT EXISTS field_sources JSONB NOT NULL DEFAULT '{}'::jsonb;

ALTER TABLE accounting_settings
    ADD COLUMN IF NOT EXISTS default_quote_notes TEXT,
    ADD COLUMN IF NOT EXISTS default_quote_terms TEXT,
    ADD COLUMN IF NOT EXISTS default_quote_signer_user_id UUID,
    ADD COLUMN IF NOT EXISTS default_quote_signer_title TEXT,
    ADD COLUMN IF NOT EXISTS default_quote_signer_phone TEXT;

DO $$
BEGIN
    IF (SELECT count(*) FROM information_schema.columns WHERE table_name = 'quotes'
          AND column_name IN ('attention_name','attention_title','signer_user_id','signer_name','signer_title',
                              'signer_phone','signer_email','field_sources')) <> 8 THEN
        RAISE EXCEPTION 'V398: kolom quotes tidak lengkap';
    END IF;
    IF (SELECT count(*) FROM information_schema.columns WHERE table_name = 'accounting_settings'
          AND column_name IN ('default_quote_notes','default_quote_terms','default_quote_signer_user_id',
                              'default_quote_signer_title','default_quote_signer_phone')) <> 5 THEN
        RAISE EXCEPTION 'V398: kolom accounting_settings tidak lengkap';
    END IF;
    IF EXISTS (SELECT 1 FROM quotes WHERE field_sources <> '{}'::jsonb OR signer_name IS NOT NULL OR attention_name IS NOT NULL) THEN
        RAISE EXCEPTION 'V398: penawaran lama tak boleh berubah';
    END IF;
END $$;
