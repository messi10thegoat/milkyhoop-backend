-- V435 (22 Okt 2026, BACKEND2, PEMBELIAN-SERAGAM P-U2): DROP trigger penimpa total Pesanan Pembelian.
-- trg_update_po_totals (AFTER INSERT/UPDATE/DELETE pada purchase_order_items) menimpa purchase_orders.subtotal/tax_amount/total_amount = Σ baris
-- dalam variabel BIGINT -> diskon & pajak HEADER hilang dari angka tersimpan dan sen terpotong; juga memicu ulang di setiap /receive & /to-bill.
-- Total kini dihitung kode (services/po_hitung, Decimal 2dp HALF_UP) di SEMUA jalur tulis (create, patch) -- kode DULU live, migrasi sesudahnya
-- (apply_mig.sh --require-marker). purchase_orders = 0 baris di produksi saat ini -> tidak ada backfill, tidak ada angka berubah.
DROP TRIGGER IF EXISTS trg_update_po_totals ON public.purchase_order_items;
DROP FUNCTION IF EXISTS public.update_purchase_order_totals();
