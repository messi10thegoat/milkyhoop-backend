-- V436 (22 Okt 2026, BACKEND2, PEMBELIAN-SERAGAM P-U2): tautan baris Tagihan Pembelian -> baris Pesanan Pembelian.
-- bill_items.purchase_order_item_id (NULL-able; ADITIF): kode /purchase-orders/{id}/to-bill mengisinya; qty/jumlah tertagih PO menjadi TURUNAN
-- bill_items bertaut pada bill tak-void (services/po_daftar.sinkron_tagih), sehingga hapus/void bill dapat MEMULIHKAN qty tertagih (G5).
-- Tanpa backfill (bills.purchase_order_id terisi = 0 di produksi). FK ON DELETE SET NULL (PO draf/terkirim boleh disunting: baris diganti).
-- Migrasi DULU (tambahan kolom tak memengaruhi kode lama), kode sesudahnya.
ALTER TABLE public.bill_items ADD COLUMN IF NOT EXISTS purchase_order_item_id uuid
    REFERENCES public.purchase_order_items(id) ON DELETE SET NULL;
CREATE INDEX IF NOT EXISTS idx_bill_items_po_item ON public.bill_items (purchase_order_item_id) WHERE purchase_order_item_id IS NOT NULL;
