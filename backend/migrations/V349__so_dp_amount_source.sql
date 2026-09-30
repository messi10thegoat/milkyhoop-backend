-- V349 (MASTER 30 Sep 2026, "default bisa-ditimpa" tahap 1): asal nominal uang muka pesanan.
-- 'percent' = dihitung SERVER dari persen x total (ikut total bila total berubah); 'manual' = diketik (terkunci).
-- NULL = SO lama (tak disentuh sampai medan DP dikirim lagi). Aditif, tanpa isi ulang (diukur 30 Sep: grapgrap 47
-- nominal-saja, 22 persen-saja, 2 keduanya -- 1 tak konsisten SO-2609-0011, dibiarkan sebagai data lama).
ALTER TABLE sales_orders
    ADD COLUMN IF NOT EXISTS dp_amount_source text NULL
        CHECK (dp_amount_source IS NULL OR dp_amount_source IN ('percent', 'manual'));

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                   WHERE table_name = 'sales_orders' AND column_name = 'dp_amount_source') THEN
        RAISE EXCEPTION 'V349: kolom sales_orders.dp_amount_source tidak mendarat';
    END IF;
END $$;
