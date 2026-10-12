-- V417 (12 Okt 2026, putusan pemilik lewat MASTER; pola SAP Production Order / NetSuite Work Order / Accurate Perintah
-- Kerja): No. SPK grapgrap terbit saat produksi DILEPAS (sesudah DP), bukan otomatis saat SO dikonfirmasi.
-- Memakai setelan yang SUDAH ADA (V359/V361 order_code_settings.trigger: 'so_confirmed' | 'manual_only'); tanpa kolom/flag
-- baru. Terbit lewat POST /sales-orders/{id}/kode-order atau opsi terbitkan_kode_order di Terima DP. Kode yang sudah
-- terbit DIBIARKAN (tak dicabut, tak dinomori ulang). Tenant lain tak berubah (bawaan tetap so_confirmed).
UPDATE order_code_settings SET trigger = 'manual_only', updated_at = now()
 WHERE tenant_id = 'grapgrap-manado' AND trigger = 'so_confirmed';

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM order_code_settings WHERE tenant_id = 'grapgrap-manado' AND trigger = 'manual_only' AND enabled) THEN
        RAISE EXCEPTION 'V417: setelan kode order grapgrap tidak menjadi manual_only (aktif)';
    END IF;
    IF EXISTS (SELECT 1 FROM order_code_settings WHERE tenant_id <> 'grapgrap-manado' AND trigger = 'manual_only') THEN
        RAISE EXCEPTION 'V417: tenant selain grapgrap ikut manual_only';
    END IF;
END $$;
