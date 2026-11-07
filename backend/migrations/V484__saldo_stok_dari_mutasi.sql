-- V484 (7 Nov 2026, BACKEND; temuan BACKEND2 BUG-099(b); nomor dipindai, tertinggi V483): get_inventory_balance = SUM(quantity_in -
-- quantity_out) inventory_ledger tenant+produk (satu rumus dgn Σ ledger / warehouse_stock / kepala Riwayat Barang). Dulu = snapshot
-- quantity_balance baris TERBARU menurut movement_date, created_at: baris bertanggal mundur (INV-2611-0008, 31 Okt, ditulis 6 Nov) dan
-- created_at kembar membuat snapshot salah (nyawit INV-0012 173 vs nyata 172). Pemanggil: services/sj_so.py:411 (saldo SJ dari SO),
-- routers/sales_invoices.py:2167 -- terukur 7 Nov, nol fungsi DB/trigger/view lain. Snapshot quantity_balance yang sudah tertulis TIDAK
-- diubah (Law 8; banksync/inventory Rule 8).
-- + DROP calculate_weighted_average(text, uuid, numeric, numeric): MATI -- nol pemanggil Python/fungsi DB/trigger/view (terukur 7 Nov);
-- WAC kanonik = inventory_helpers.record_inventory_inbound / get_weighted_average_cost (V480). Kode bersih: yang digantikan DIHAPUS.
-- Penerapan: pemilik LANGSUNG sesudah Selasa. JALANKAN lewat apply_mig.sh (psql -v ON_ERROR_STOP=1 -1; tanpa BEGIN/COMMIT).
-- Pembalik: V484__saldo_stok_dari_mutasi_ROLLBACK.sql.

CREATE OR REPLACE FUNCTION public.get_inventory_balance(p_tenant_id text, p_product_id uuid)
 RETURNS numeric
 LANGUAGE plpgsql
AS $function$
DECLARE
    v_balance NUMERIC;
BEGIN
    -- BUG-099(b): saldo = Σ mutasi (bukan snapshot baris terbaru)
    SELECT COALESCE(SUM(COALESCE(quantity_in, 0) - COALESCE(quantity_out, 0)), 0) INTO v_balance
    FROM inventory_ledger
    WHERE tenant_id = p_tenant_id AND product_id = p_product_id;

    RETURN v_balance;
END;
$function$;

DROP FUNCTION IF EXISTS public.calculate_weighted_average(text, uuid, numeric, numeric);
