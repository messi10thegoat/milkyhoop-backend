-- V405 (MASTER 11 Okt 2026, F5 opsi B) -- flag CAKUPAN kaos saja: conversational_master_inline_so_si = panel
-- Pelanggan/Barang (flag entitas V403/V404) JUGA di form CW SO & Faktur. Tanpa flag ini SO/Faktur jalur lama persis.
-- grapgrap-manado WAJIB NOL: izin pemilik 11 Okt hanya untuk Penawaran (V404).
INSERT INTO tenant_features (tenant_id, feature)
SELECT 'kaos-biru-konveksi', 'conversational_master_inline_so_si'
WHERE EXISTS (SELECT 1 FROM "Tenant" WHERE id = 'kaos-biru-konveksi')
ON CONFLICT (tenant_id, feature) DO NOTHING;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM tenant_features WHERE tenant_id = 'kaos-biru-konveksi' AND enabled
          AND feature = 'conversational_master_inline_so_si') THEN
        RAISE EXCEPTION 'V405: flag cakupan master inline SO/Faktur kaos tidak mendarat/aktif';
    END IF;
    IF EXISTS (SELECT 1 FROM tenant_features WHERE tenant_id = 'grapgrap-manado'
          AND feature = 'conversational_master_inline_so_si') THEN
        RAISE EXCEPTION 'V405: flag cakupan master inline SO/Faktur ada di grapgrap-manado (wajib NOL; izin pemilik hanya Penawaran)';
    END IF;
    IF EXISTS (SELECT 1 FROM tenant_features WHERE tenant_id <> 'kaos-biru-konveksi'
          AND feature = 'conversational_master_inline_so_si') THEN
        RAISE EXCEPTION 'V405: flag cakupan master inline SO/Faktur ditemukan di tenant lain';
    END IF;
END $$;
