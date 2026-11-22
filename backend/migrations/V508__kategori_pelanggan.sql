-- V508: KATEGORI PELANGGAN (BUG-338 bagian A; putusan pemilik 22 Nov 2026: ikut Accurate -- Kategori Penjualan -> Tingkat Harga).
-- Kategori = master per tenant; pelanggan menempel ke SATU kategori (customers.category_id, NULL = tanpa kategori); kategori menunjuk
-- SATU daftar harga (price_list_id, NULL = tanpa). Prioritas harga: DH pelanggan > DH kategori > DH bawaan (services/harga_jual.daftar_untuk).
-- ADITIF: 0 baris lama disentuh, tanpa backfill (perilaku harga pelanggan lama TIDAK berubah). FK KOMPOSIT (tenant_id, ...) menutup silang tenant
-- di DB; tanpa ON DELETE SET NULL (akan meng-NULL tenant_id NOT NULL) -- hapus kategori/DH yang dipakai DIBLOK kode (409).
-- Pembalik: V508__kategori_pelanggan_ROLLBACK.sql.
ALTER TABLE price_lists ADD CONSTRAINT uq_price_lists_tenant_id UNIQUE (tenant_id, id);

CREATE TABLE customer_categories (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id     TEXT NOT NULL,
    name          TEXT NOT NULL,
    price_list_id UUID NULL,
    is_active     BOOLEAN NOT NULL DEFAULT true,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_cc_tenant_id UNIQUE (tenant_id, id),
    CONSTRAINT chk_cc_nama CHECK (btrim(name) <> ''),
    CONSTRAINT fk_cc_dh FOREIGN KEY (tenant_id, price_list_id) REFERENCES price_lists (tenant_id, id)
);
CREATE UNIQUE INDEX uq_cc_nama ON customer_categories (tenant_id, lower(btrim(name)));
CREATE INDEX idx_cc_dh ON customer_categories (tenant_id, price_list_id) WHERE price_list_id IS NOT NULL;
ALTER TABLE customer_categories ENABLE ROW LEVEL SECURITY;
CREATE POLICY rls_customer_categories ON customer_categories FOR ALL USING (tenant_id = current_setting('app.tenant_id', true));

ALTER TABLE customers ADD COLUMN category_id UUID NULL;
ALTER TABLE customers ADD CONSTRAINT fk_cust_cat FOREIGN KEY (tenant_id, category_id) REFERENCES customer_categories (tenant_id, id);
CREATE INDEX idx_customers_category ON customers (tenant_id, category_id) WHERE category_id IS NOT NULL;
