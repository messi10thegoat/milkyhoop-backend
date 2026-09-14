-- V257 (14 Sep 2026) — item_categories: registry kategori item SATU SUMBER + auto-register di DB.
-- Latar: POST /api/items/categories dulu cuma cek unik lalu balas "siap digunakan" TANPA menyimpan;
-- GET = DISTINCT products.kategori → kategori yang dibuat sebelum dipakai item LENYAP, tak terkelola.
-- Putusan MASTER: GET = tabel SAJA (bukan union, supaya DELETE mungkin). Auto-register di SEMUA
-- penulis products.kategori. Diukur: penulis = items.py (create/update/varian) + matrix_items.py;
-- semua lewat DML `products`. Karena itu chokepoint = TRIGGER di products (satu tempat menjamin SEMUA
-- penulis — dashboard/bot/import/SQL/masa depan), sesuai prinsip ironlaws "invariant ditegakkan di DB".

CREATE TABLE IF NOT EXISTS item_categories (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id text NOT NULL,
    name text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    created_by text
);
-- Unik case-insensitive per tenant; nama disimpan sesuai input pertama (casing).
CREATE UNIQUE INDEX IF NOT EXISTS uq_item_categories_tenant_lower ON item_categories (tenant_id, lower(name));

-- Backfill: casing dari produk TERAWAL per (tenant, lower(kategori)). 0 perubahan pada products.
INSERT INTO item_categories (tenant_id, name)
SELECT DISTINCT ON (p.tenant_id, lower(p.kategori)) p.tenant_id, p.kategori
FROM products p
WHERE p.kategori IS NOT NULL AND p.kategori <> ''
ORDER BY p.tenant_id, lower(p.kategori), p.created_at ASC
ON CONFLICT (tenant_id, lower(name)) DO NOTHING;

-- Auto-register: setiap tulis products.kategori (INSERT / UPDATE OF kategori) → daftarkan.
CREATE OR REPLACE FUNCTION register_item_category() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.kategori IS NOT NULL AND NEW.kategori <> '' THEN
        INSERT INTO item_categories (tenant_id, name)
        VALUES (NEW.tenant_id, NEW.kategori)
        ON CONFLICT (tenant_id, lower(name)) DO NOTHING;  -- casing pertama menang; tak buat duplikat
    END IF;
    RETURN NEW;
END;
$$;
DROP TRIGGER IF EXISTS trg_products_kategori_register ON products;
CREATE TRIGGER trg_products_kategori_register
    AFTER INSERT OR UPDATE OF kategori ON products
    FOR EACH ROW
    WHEN (NEW.kategori IS NOT NULL AND NEW.kategori <> '')
    EXECUTE FUNCTION register_item_category();

-- ASSERT invariant (0 selisih): tiap DISTINCT lower(products.kategori) HARUS ada di item_categories.
DO $$
DECLARE v_missing bigint;
BEGIN
    SELECT count(*) INTO v_missing FROM (
        SELECT DISTINCT p.tenant_id, lower(p.kategori) AS lk
        FROM products p WHERE p.kategori IS NOT NULL AND p.kategori <> ''
    ) d
    LEFT JOIN item_categories ic ON ic.tenant_id = d.tenant_id AND lower(ic.name) = d.lk
    WHERE ic.id IS NULL;
    IF v_missing <> 0 THEN
        RAISE EXCEPTION 'V257: % kategori products tak masuk item_categories (backfill invariant gagal)', v_missing;
    END IF;
    RAISE NOTICE 'V257 OK: invariant subset terpenuhi (0 selisih).';
END $$;
