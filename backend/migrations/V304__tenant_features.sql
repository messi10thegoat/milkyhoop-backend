-- V304 (W0 Conversational Workspace) — flag fitur per tenant.
--
-- Satu baris = satu flag untuk satu tenant. Flag yang TAK tercantum (atau enabled=false)
-- = mati. Dibaca oleh GET /api/permissions/me -> medan `features: string[]` (flag AKTIF
-- saja, terurut). Menyalakan/mematikan = satu UPDATE, tanpa deploy.
--
-- Seed: conversational_workspace_so + conversational_form_so HANYA untuk grapgrap-manado
-- (putusan pemilik via paket spek 25 Sep 2026: rilis bertahap, grapgrap dulu).
-- Aditif murni: tabel baru, nol perubahan pada tabel lain. Rilis = migrasi-saja DULUAN.

CREATE TABLE IF NOT EXISTS tenant_features (
    tenant_id  text        NOT NULL REFERENCES "Tenant"(id) ON DELETE CASCADE,
    feature    text        NOT NULL CHECK (feature ~ '^[a-z][a-z0-9_]{2,63}$'),
    enabled    boolean     NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, feature)
);

ALTER TABLE tenant_features ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS rls_tenant_features ON tenant_features;
CREATE POLICY rls_tenant_features ON tenant_features
    USING (tenant_id = current_setting('app.tenant_id', true));

INSERT INTO tenant_features (tenant_id, feature)
SELECT 'grapgrap-manado', f
FROM unnest(ARRAY['conversational_workspace_so', 'conversational_form_so']) AS f
WHERE EXISTS (SELECT 1 FROM "Tenant" WHERE id = 'grapgrap-manado')
ON CONFLICT (tenant_id, feature) DO NOTHING;

-- Gagal keras bila seed tak mendarat (mis. id tenant grapgrap berubah): flag yang diam-diam
-- tak menyala terlihat persis seperti "fitur belum dirilis".
DO $$
BEGIN
    IF (SELECT count(*) FROM tenant_features WHERE tenant_id = 'grapgrap-manado' AND enabled) < 2 THEN
        RAISE EXCEPTION 'V304: seed flag grapgrap-manado tidak lengkap';
    END IF;
END $$;
