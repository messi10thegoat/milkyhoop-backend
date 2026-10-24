-- V465 (24 Okt 2026, CONVERSATIONAL; blok V465-V469 dari MASTER): tabel audit Milkybook `asisten_aksi` (append-only).
-- Rancangan FE docs/asisten-rancangan §3/§11.11. Penulis: app/milkybook/inti/audit.py AuditPostgres (kolom == _SQL, dikunci tes).
-- Tanpa prompt mentah / payload mentah: hanya hash + id. PII hanya sebagai id.
-- Schema-first (diukur 24 Okt, read-only): nama asisten_aksi / asisten_aksi_tolak_ubah / indeks asisten_aksi% = 0 di milkydb;
-- tenant_id = TEXT (sama dengan tenant_features, quotes); user_id TEXT tanpa FK (audit append-only; quotes.created_by = uuid).
-- Penerapan: merge_branch.sh (hanya migrasi) + apply_mig.sh dengan cadangan pg_dump; HANYA izin pemilik langsung + slot MASTER.
-- Nol data lama disentuh (tabel baru).
CREATE TABLE IF NOT EXISTS asisten_aksi (
    id                BIGSERIAL PRIMARY KEY,
    tenant_id         TEXT        NOT NULL,
    user_id           TEXT        NOT NULL,
    sesi_id           TEXT        NOT NULL,
    peristiwa         TEXT        NOT NULL CHECK (peristiwa IN
                        ('tool','kartu_lahir','kartu_setuju','kartu_tolak','kartu_basi','penjaga_angka','model','giliran')),
    tool              TEXT,
    kelas             TEXT,
    model             TEXT,
    prompt_hash       TEXT,
    payload_hash      TEXT,
    kunci_idempotensi TEXT,
    kartu_id          TEXT,
    disetujui_oleh    TEXT,
    status_be         INTEGER,
    hasil             TEXT,
    token_masuk       INTEGER NOT NULL DEFAULT 0,
    token_keluar      INTEGER NOT NULL DEFAULT 0,
    token_cache_baca  INTEGER NOT NULL DEFAULT 0,
    token_cache_tulis INTEGER NOT NULL DEFAULT 0,
    eskalasi          BOOLEAN,
    biaya_usd         NUMERIC(12,6),
    dibuat            TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS asisten_aksi_tenant_dibuat ON asisten_aksi (tenant_id, dibuat DESC);
CREATE INDEX IF NOT EXISTS asisten_aksi_giliran ON asisten_aksi (tenant_id, dibuat) WHERE peristiwa = 'giliran';
-- Append-only: tolak UPDATE/DELETE.
CREATE OR REPLACE FUNCTION asisten_aksi_tolak_ubah() RETURNS trigger AS $$
BEGIN RAISE EXCEPTION 'asisten_aksi append-only'; END; $$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS asisten_aksi_append_only ON asisten_aksi;
CREATE TRIGGER asisten_aksi_append_only BEFORE UPDATE OR DELETE ON asisten_aksi
    FOR EACH ROW EXECUTE FUNCTION asisten_aksi_tolak_ubah();
