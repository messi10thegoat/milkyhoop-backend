-- V399 (MASTER GO 7 Okt 2026, pemilik: "ikut rekomendasi, pastikan proper akuntansi & pola SaaS mapan") -- FASE LOKASI pesanan penjualan:
-- tahap operasional "Dikirim ke {gudang}" / "Tersedia di {gudang}" (produksi di Bandung, toko & pengambilan di Manado).
--
-- DATA OPERASIONAL SAJA: NOL jurnal, NOL stok, NOL HPP (barang non_inventory tak punya stok; Law 1/16: angka uang tetap turunan jurnal,
-- kolom ini bukan angka). Aditif; baris lama = NULL (tak punya fase). Hanya sales_orders yang disentuh (+ kunci unik id+tenant pada
-- warehouses supaya FK gudang WAJIB satu tenant dengan pesanan -- Law 24 ditegakkan di DB, bukan hanya di kode).
--
-- Idempoten; gagal keras bila tak mendarat atau bila ada baris lama yang terisi.

ALTER TABLE sales_orders
    ADD COLUMN IF NOT EXISTS fase_lokasi text,
    ADD COLUMN IF NOT EXISTS fase_gudang_id uuid,
    ADD COLUMN IF NOT EXISTS fase_at timestamptz;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'chk_so_fase_lokasi') THEN
        ALTER TABLE sales_orders ADD CONSTRAINT chk_so_fase_lokasi
            CHECK (fase_lokasi IS NULL OR fase_lokasi IN ('dikirim_ke_lokasi', 'tersedia'));
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'chk_so_fase_lengkap') THEN
        -- fase, gudang, dan waktu: ketiganya terisi ATAU ketiganya kosong (tak ada fase setengah jadi)
        ALTER TABLE sales_orders ADD CONSTRAINT chk_so_fase_lengkap
            CHECK ((fase_lokasi IS NULL) = (fase_gudang_id IS NULL) AND (fase_lokasi IS NULL) = (fase_at IS NULL));
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'uq_warehouses_id_tenant') THEN
        ALTER TABLE warehouses ADD CONSTRAINT uq_warehouses_id_tenant UNIQUE (id, tenant_id);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_so_fase_gudang') THEN
        ALTER TABLE sales_orders ADD CONSTRAINT fk_so_fase_gudang
            FOREIGN KEY (fase_gudang_id, tenant_id) REFERENCES warehouses (id, tenant_id);  -- NO ACTION: gudang yang dipakai tak bisa dihapus
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_so_fase_lokasi ON sales_orders (tenant_id, fase_lokasi) WHERE fase_lokasi IS NOT NULL;

DO $$
BEGIN
    IF (SELECT count(*) FROM information_schema.columns WHERE table_name = 'sales_orders'
          AND column_name IN ('fase_lokasi', 'fase_gudang_id', 'fase_at')) <> 3 THEN
        RAISE EXCEPTION 'V399: kolom fase tidak lengkap di sales_orders';
    END IF;
    IF (SELECT count(*) FROM pg_constraint WHERE conname IN
          ('chk_so_fase_lokasi', 'chk_so_fase_lengkap', 'uq_warehouses_id_tenant', 'fk_so_fase_gudang')) <> 4 THEN
        RAISE EXCEPTION 'V399: constraint tidak lengkap';
    END IF;
    IF EXISTS (SELECT 1 FROM sales_orders WHERE fase_lokasi IS NOT NULL OR fase_gudang_id IS NOT NULL OR fase_at IS NOT NULL) THEN
        RAISE EXCEPTION 'V399: ada baris SO berisi fase setelah migrasi (harus kosong)';
    END IF;
END $$;
