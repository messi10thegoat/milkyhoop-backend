-- V480 (6 Nov 2026, BACKEND2; nomor dipesan MASTER): BUG-057 WAC BASI. Putusan pemilik LANGSUNG 6 Nov (sesi BACKEND2): get_weighted_average_cost
-- = Σ nilai bersih / Σ qty bersih on-hand (rumus SAMA dengan inventory_helpers.record_inventory_inbound, health check 15
-- verify_inventory_wac_reconciliation_all, dan revaluasi_ppv). Dulu = average_cost baris kartu stok TERBARU menurut movement_date:
--   * pembalik void (record_inventory_reversal) MENYALIN average lama ("snapshot, no recalc") -> WAC basi sesudah tagihan/GRN dibatalkan;
--   * baris bertanggal mundur (tanggal dokumen < baris lain) kalah urutan -> average-nya tak pernah terbaca.
--   Terukur 6 Nov (semua tenant, baca saja, izin pemilik): 3 produk kaos + 2 produk nyawit (Bimoli: terbaca 36.925 / 18.990,
--   benar 38.850 / 19.980); keluar barang tercatat pada biaya beda: kaos 5 baris PRODUCTION_OUTPUT (-75.048,42); nyawit 0.
-- Cadangan TIDAK berubah: qty on-hand <= 0 atau nilai < 0 -> average_cost baris terakhir (perilaku lama) -> products.purchase_price -> 0.
-- Pemanggil (faktur/SJ/nota kredit/produksi/pemindahan/revaluasi; nol fungsi DB lain) tak perlu diubah. Nol data diubah (Law 8);
-- HPP yang sudah tercatat tidak disentuh (Law 2). Pembalik: V480__wac_dari_nilai_per_qty_ROLLBACK.sql.
-- JALANKAN lewat apply_mig.sh (psql -v ON_ERROR_STOP=1 -1; tanpa BEGIN/COMMIT di berkas).

CREATE OR REPLACE FUNCTION public.get_weighted_average_cost(p_tenant_id text, p_product_id uuid)
 RETURNS numeric
 LANGUAGE plpgsql
AS $function$
DECLARE
    v_qty NUMERIC;
    v_nilai NUMERIC;
    v_avg_cost NUMERIC;
    v_fallback_cost NUMERIC;
BEGIN
    -- BUG-057: SATU rumus WAC = nilai bersih / qty bersih on-hand (Σ qty_in x unit_cost - Σ qty_out x unit_cost)
    SELECT COALESCE(SUM(quantity_in) - SUM(quantity_out), 0),
           COALESCE(SUM(quantity_in * unit_cost) - SUM(quantity_out * unit_cost), 0)
      INTO v_qty, v_nilai
    FROM inventory_ledger
    WHERE tenant_id = p_tenant_id
      AND product_id = p_product_id;

    IF v_qty > 0 AND v_nilai >= 0 THEN
        RETURN ROUND(v_nilai / v_qty, 2);
    END IF;

    -- cadangan lama (stok habis / tak wajar): average_cost baris terakhir
    SELECT average_cost INTO v_avg_cost
    FROM inventory_ledger
    WHERE tenant_id = p_tenant_id
      AND product_id = p_product_id
      AND average_cost IS NOT NULL
    ORDER BY movement_date DESC, created_at DESC
    LIMIT 1;

    IF v_avg_cost IS NOT NULL THEN
        RETURN v_avg_cost;
    END IF;

    SELECT purchase_price INTO v_fallback_cost
    FROM products
    WHERE tenant_id = p_tenant_id AND id = p_product_id;

    RETURN COALESCE(v_fallback_cost, 0);
END;
$function$;

-- penjaga: definisi baru benar-benar terpasang (rumus nilai/qty ada)
DO $$
BEGIN
    IF pg_get_functiondef('public.get_weighted_average_cost(text, uuid)'::regprocedure) NOT LIKE '%v_nilai / v_qty%' THEN
        RAISE EXCEPTION 'V480: get_weighted_average_cost tidak terpasang';
    END IF;
END $$;
