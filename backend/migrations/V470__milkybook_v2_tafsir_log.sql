-- V470: LOG KETIKAN lapisan LLM Milkybook v2 (1 Nov 2026, CONVERSATIONAL; nomor dipesan MASTER; paket v2 03 §4).
-- Satu baris per panggilan POST /api/milkybook/v2/tafsir: ketikan pengguna, tahap draf, maksud hasil, sumber jawaban,
-- latensi, model, biaya. Tujuan: kalimat yang sering lolos ke LLM ditambang menjadi aturan baru (jalur elastis).
-- Putusan pemilik LANGSUNG 1 Nov: teks ketikan disimpan 30 hari. Ketikan = data pelanggan (nama/HP bisa ada):
--   tenant_id + user_id di setiap baris; dihapus sungguhan > 30 hari saat menulis (app/milkybook/v2/tafsir.py
--   SQL_BUANG_LOG); tidak pernah ditulis ke stdout/logger aplikasi.
-- Biaya untuk pagar biaya TIDAK dibaca dari sini (pagar = asisten_aksi 'giliran', sama dengan v1).
-- ADITIF: tabel baru, nol data lama disentuh. Pembalik: V470__milkybook_v2_tafsir_log_ROLLBACK.sql.
CREATE TABLE IF NOT EXISTS milkybook_v2_tafsir_log (
    id         BIGSERIAL   PRIMARY KEY,
    tenant_id  TEXT        NOT NULL,
    user_id    TEXT        NOT NULL,
    tahap      TEXT,
    ketikan    TEXT        NOT NULL CHECK (char_length(ketikan) <= 500),
    intent     TEXT        NOT NULL,
    sumber     TEXT        NOT NULL CHECK (sumber IN ('llm', 'batas', 'galat', 'tak_sah', 'penjaga')),
    latensi_ms INTEGER     NOT NULL DEFAULT 0 CHECK (latensi_ms >= 0),
    model      TEXT,
    biaya_usd  NUMERIC(12,6),
    dibuat     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS mb2_tafsir_log_tenant_dibuat ON milkybook_v2_tafsir_log (tenant_id, dibuat);
