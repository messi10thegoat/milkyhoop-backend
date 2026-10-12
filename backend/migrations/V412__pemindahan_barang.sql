-- V412 (BACKEND2 12 Okt 2026; kontrak R2 Pemindahan Barang, putusan MASTER P3/P5/P6/P7) -- JALANKAN dengan:
--   psql -v ON_ERROR_STOP=1 -1   (satu transaksi; tanpa -1 sebuah guard yang gagal meninggalkan separuh perubahan)
-- Tabel stock_transfers/_items SUDAH ADA dan KOSONG (0 baris di semua tenant, diukur 12 Okt): yang dibangun ulang = LOGIKA tulis.
--   1. status baru `completed` (satu langkah: kirim+terima atomik) dan `voided` (pembalik) di chk_st_status.
--   2. kolom sales_order_id (PB boleh ditautkan ke satu SO -> fase "tersedia"), voided_at/by/void_reason.
--   3. prevent_st_modification: `completed` ikut dibekukan (hanya boleh berubah ke `voided`).
--   4. DROP ship_stock_transfer / receive_stock_transfer: keduanya menulis kolom HANTU inventory_ledger(item_id,
--      quantity_change, total_value, transaction_date) -> tak pernah bisa jalan. Dilumpuhkan, bukan dibiarkan (Law 33 #7).
-- Tak ada akun CoA, jurnal, atau peran akun baru. Tak ada baris data diubah.
DO $$
DECLARE n bigint;
BEGIN
    SELECT count(*) INTO n FROM stock_transfers;
    IF n <> 0 THEN
        RAISE EXCEPTION 'V412: stock_transfers berisi % baris; migrasi mengandaikan tabel kosong (bangun ulang logika, bukan data)', n;
    END IF;
END $$;

ALTER TABLE stock_transfers DROP CONSTRAINT IF EXISTS chk_st_status;
ALTER TABLE stock_transfers ADD CONSTRAINT chk_st_status
    CHECK (status::text = ANY (ARRAY['draft','in_transit','received','cancelled','completed','voided']));

ALTER TABLE stock_transfers ADD COLUMN IF NOT EXISTS sales_order_id uuid REFERENCES sales_orders(id);
ALTER TABLE stock_transfers ADD COLUMN IF NOT EXISTS voided_at timestamptz;
ALTER TABLE stock_transfers ADD COLUMN IF NOT EXISTS voided_by uuid;
ALTER TABLE stock_transfers ADD COLUMN IF NOT EXISTS void_reason text;
CREATE INDEX IF NOT EXISTS idx_stock_transfers_so ON stock_transfers (tenant_id, sales_order_id) WHERE sales_order_id IS NOT NULL;

CREATE OR REPLACE FUNCTION prevent_st_modification() RETURNS trigger LANGUAGE plpgsql AS $function$
BEGIN
    IF OLD.status IN ('in_transit', 'received') AND TG_OP = 'UPDATE' THEN
        -- Allow only status changes and receiving
        IF NEW.status != OLD.status OR
           NEW.received_by IS DISTINCT FROM OLD.received_by OR
           NEW.received_date IS DISTINCT FROM OLD.received_date THEN
            RETURN NEW;
        END IF;
        RAISE EXCEPTION 'Cannot modify stock transfer after shipping';
    END IF;
    IF OLD.status IN ('completed', 'voided') AND TG_OP = 'UPDATE' THEN
        -- Pemindahan selesai = fakta stok: hanya boleh menjadi `voided` (pembalik ledger ditulis aplikasi). `voided` BEKU.
        IF OLD.status = 'completed' AND NEW.status = 'voided' THEN
            RETURN NEW;
        END IF;
        -- updated_at/total diperbarui trigger lain; kolom fakta (gudang, tanggal, status, SO) tak boleh berubah
        IF NEW.from_warehouse_id IS DISTINCT FROM OLD.from_warehouse_id
           OR NEW.to_warehouse_id IS DISTINCT FROM OLD.to_warehouse_id
           OR NEW.status IS DISTINCT FROM OLD.status
           OR NEW.shipped_date IS DISTINCT FROM OLD.shipped_date
           OR NEW.received_date IS DISTINCT FROM OLD.received_date
           OR NEW.sales_order_id IS DISTINCT FROM OLD.sales_order_id THEN
            RAISE EXCEPTION 'Cannot modify completed or voided stock transfer';
        END IF;
    END IF;
    RETURN NEW;
END;
$function$;

DROP FUNCTION IF EXISTS ship_stock_transfer(uuid, uuid);
DROP FUNCTION IF EXISTS receive_stock_transfer(uuid, uuid, jsonb);

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_proc WHERE proname IN ('ship_stock_transfer', 'receive_stock_transfer')) THEN
        RAISE EXCEPTION 'V412: fungsi transfer lama (kolom hantu) masih ada';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'stock_transfers' AND column_name = 'sales_order_id') THEN
        RAISE EXCEPTION 'V412: kolom sales_order_id tidak terpasang';
    END IF;
END $$;
