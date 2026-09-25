-- V305 (W3/U4 Conversational Workspace, Q-009) — alias item per tenant.
--
-- Satu baris = satu teks yang sudah dinormalisasi (lower + trim + spasi tunggal) yang
-- menunjuk satu item, dipakai bersama semua admin/browser tenant itu. Dua bentuk teks:
-- singkatan ("combed 50") dan pilihan varian ("kaos polos hitam|xl").
-- Dibaca/ditulis lewat GET/PUT /api/items/aliases (routers/item_aliases.py).
--
-- CATATAN JUJUR (Law 34):
-- * Item dihapus-LUNAK (products.deleted_at). ON DELETE CASCADE hanya berlaku untuk
--   hapus-KERAS; "item dihapus -> alias hilang" dijamin oleh filter GET, bukan FK ini.
-- * Kesamaan tenant alias vs item dijaga di handler (tes dua sisi), BUKAN di DB:
--   pagar DB butuh UNIQUE(id, tenant_id) pada products — ditunda.
-- Aditif murni: tabel baru, nol perubahan pada tabel lain. Rilis = migrasi-saja DULUAN.

CREATE TABLE IF NOT EXISTS item_aliases (
    id         uuid         PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id  text         NOT NULL REFERENCES "Tenant"(id) ON DELETE CASCADE,
    teks       varchar(120) NOT NULL
               CHECK (teks = btrim(teks) AND teks !~ '\s\s' AND teks !~ '[\t\n\r]' AND length(teks) BETWEEN 1 AND 120),
    item_id    uuid         NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    created_by uuid         NULL,
    created_at timestamptz  NOT NULL DEFAULT now(),
    updated_at timestamptz  NOT NULL DEFAULT now(),
    CONSTRAINT uq_item_aliases_tenant_teks UNIQUE (tenant_id, teks)
);

CREATE INDEX IF NOT EXISTS ix_item_aliases_tenant_item ON item_aliases (tenant_id, item_id);

ALTER TABLE item_aliases ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS rls_item_aliases ON item_aliases;
CREATE POLICY rls_item_aliases ON item_aliases
    USING (tenant_id = current_setting('app.tenant_id', true));
