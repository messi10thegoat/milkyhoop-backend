-- V476 DAFTAR-HARGA pembersihan (4 Nov 2026; aturan tetap pemilik: yang digantikan DIHAPUS). Diterapkan SESUDAH kode DAFTAR HARGA
-- live (kode lama gabung-pelanggan masih membaca customer_price_lists). Digantikan: fungsi DB get_item_price (bigint, satuan bocor)
-- -> services/harga_jual; customer_price_lists (banyak-ke-banyak, 0 baris) -> customers.price_list_id (V474).
-- Pagar: tolak bila tabel ternyata berisi (pembersihan tak boleh membuang data).
DO $$
BEGIN
    IF to_regclass('public.customer_price_lists') IS NOT NULL THEN  -- bersarang: plpgsql merencanakan kondisi utuh
        IF EXISTS (SELECT 1 FROM public.customer_price_lists) THEN
            RAISE EXCEPTION 'customer_price_lists berisi baris; pembersihan dibatalkan';
        END IF;
    END IF;
END $$;

DROP FUNCTION IF EXISTS public.get_item_price(text, uuid, uuid, numeric, character varying);
DROP TABLE IF EXISTS public.customer_price_lists;
