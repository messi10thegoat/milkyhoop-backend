-- V469: DRAF PERCAKAPAN Milkybook v2 (1 Nov 2026, CONVERSATIONAL; nomor dipesan MASTER; paket v2 03 §1).
-- Satu baris = satu percakapan milik SATU pengguna di SATU tenant: draf aktif (maks satu) + draf yang di-park.
-- Draf BUKAN data pembukuan (nol jurnal, nol dokumen); jadi penawaran hanya lewat POST /api/milkybook/v2/penawaran.
-- Umur 7 hari sejak diubah terakhir: dibaca dengan saringan 7 hari DAN dihapus sungguhan saat pengguna menulis
-- (app/milkybook/v2/draf.py SQL_BUANG_BASI) -> tidak menumpuk selamanya.
-- Postgres, bukan Redis: lanjutkan dari perangkat lain selama 7 hari harus tahan restart/eviksi Redis.
-- tenant_id/user_id TEXT (sama dengan asisten_aksi V465). Isi draf = data pelanggan: tidak pernah ditulis ke log.
-- ADITIF: tabel baru, nol data lama disentuh. Pembalik: V469__milkybook_v2_percakapan_ROLLBACK.sql.
CREATE TABLE IF NOT EXISTS milkybook_v2_percakapan (
    id        UUID        PRIMARY KEY,
    tenant_id TEXT        NOT NULL,
    user_id   TEXT        NOT NULL,
    judul     TEXT        CHECK (judul IS NULL OR char_length(judul) <= 120),
    draf      JSONB       CHECK (draf IS NULL OR jsonb_typeof(draf) = 'object'),
    parkir    JSONB       NOT NULL DEFAULT '[]'::jsonb CHECK (jsonb_typeof(parkir) = 'array'),
    versi     INTEGER     NOT NULL DEFAULT 1 CHECK (versi >= 1),
    dibuat    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    diubah    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT chk_mb2_percakapan_ukuran CHECK (octet_length(COALESCE(draf::text, '')) + octet_length(parkir::text) <= 262144)
);
CREATE INDEX IF NOT EXISTS mb2_percakapan_pemilik_diubah ON milkybook_v2_percakapan (tenant_id, user_id, diubah DESC);
