-- Pembalik V510. MENOLAK bila sudah ada lapisan: sesudah transaksi FIFO, mundur = jurnal penyesuaian, bukan DROP.
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM inventory_cost_layers) THEN RAISE EXCEPTION 'V510 ROLLBACK ditolak: sudah ada lapisan FIFO'; END IF;
END $$;
DROP TRIGGER trg_jaga_metode_biaya ON products;
DROP FUNCTION trg_jaga_metode_biaya();
DROP TABLE inventory_layer_moves;
DROP TABLE inventory_cost_layers;
DROP FUNCTION trg_jaga_gerak_lapisan();
DROP FUNCTION trg_lapisan_tambah_saja();
ALTER TABLE products DROP COLUMN costing_mulai;
ALTER TABLE tenant_config DROP COLUMN costing_method_bawaan;
