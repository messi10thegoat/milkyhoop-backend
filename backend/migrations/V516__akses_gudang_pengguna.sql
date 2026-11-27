-- V516: AKSES GUDANG per pengguna (PARITAS-4b, ikut Accurate; GO pemilik LANGSUNG 28 Nov ±05:50 di sesi BACKEND 3).
-- ADITIF, 0 data, tanpa backfill: pengguna TANPA baris = SEMUA gudang (perilaku lama utuh). OWNER/ADMIN selalu semua (penentu
-- services/akses_gudang). Pembatasan hanya berlaku bila flag gelap akses_gudang_v1 NYALA di tenant.
-- Pembalik: V516__akses_gudang_pengguna_ROLLBACK.sql. Tanpa BEGIN/COMMIT (apply_mig).
CREATE TABLE user_warehouse_access (
    tenant_id    TEXT NOT NULL,
    user_id      UUID NOT NULL,
    warehouse_id UUID NOT NULL REFERENCES warehouses(id) ON DELETE CASCADE,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by   UUID,
    PRIMARY KEY (tenant_id, user_id, warehouse_id)
);
CREATE INDEX idx_uwa_tenant_gudang ON user_warehouse_access (tenant_id, warehouse_id);
ALTER TABLE user_warehouse_access ENABLE ROW LEVEL SECURITY;
CREATE POLICY rls_user_warehouse_access ON user_warehouse_access FOR ALL USING (tenant_id = current_setting('app.tenant_id', true));
