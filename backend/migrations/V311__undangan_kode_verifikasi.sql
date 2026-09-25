-- V311 (26 Sep 2026, audit WRITE_EXEMPT usul (a), putusan MASTER; DEPLOY menunggu pemilik)
-- Menerima undangan sebagai AKUN BARU kini wajib kode 6 digit yang dikirim ke email UNDANGAN.
-- Dulu: pemegang token (pengundang selalu menerima invite_link) bisa membuat akun untuk email
-- orang lain dengan sandi pilihannya, isVerified=true -> email "diklaim" tanpa bukti kepemilikan.
-- Aditif saja: kolom baru bernilai NULL/0 untuk undangan lama.
ALTER TABLE team_invitations
    ADD COLUMN IF NOT EXISTS verify_code_hash       TEXT,
    ADD COLUMN IF NOT EXISTS verify_code_expires_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS verify_attempts        INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS verify_sent_at         TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS verify_sent_window_at  TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS verify_sent_count      INTEGER NOT NULL DEFAULT 0;

DO $$
BEGIN
    IF (SELECT count(*) FROM information_schema.columns
        WHERE table_name = 'team_invitations'
          AND column_name IN ('verify_code_hash', 'verify_code_expires_at', 'verify_attempts',
                              'verify_sent_at', 'verify_sent_window_at', 'verify_sent_count')) <> 6 THEN
        RAISE EXCEPTION 'V311: kolom verifikasi undangan tidak lengkap';
    END IF;
END $$;
