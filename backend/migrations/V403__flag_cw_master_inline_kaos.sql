-- V403 (MASTER 10 Okt 2026, pilot "master inline di Penawaran") -- flag KAOS SAJA:
-- conversational_customer_inline = F2 panel Pelanggan cepat/lengkap (WORKSPACE);
-- conversational_item_inline     = F3 panel Barang cepat/lengkap (FRONTEND).
-- BE pendukung LIVE 6ecb9054 + V402 (409 berkode, default_item_type/default_bisa_dikirim). grapgrap TIDAK (migrasi
-- terpisah sesudah gerbang + nyata kaos hijau). Dibaca /permissions/me -> nol kode, nol restart. Idempoten; gagal keras
-- bila tak mendarat ATAU tenant lain ikut memilikinya.

INSERT INTO tenant_features (tenant_id, feature)
SELECT 'kaos-biru-konveksi', f
FROM unnest(ARRAY['conversational_customer_inline', 'conversational_item_inline']) AS f
WHERE EXISTS (SELECT 1 FROM "Tenant" WHERE id = 'kaos-biru-konveksi')
ON CONFLICT (tenant_id, feature) DO NOTHING;

DO $$
BEGIN
    IF (SELECT count(*) FROM tenant_features WHERE tenant_id = 'kaos-biru-konveksi' AND enabled
          AND feature IN ('conversational_customer_inline', 'conversational_item_inline')) <> 2 THEN
        RAISE EXCEPTION 'V403: flag master inline kaos tidak mendarat/aktif (harus 2)';
    END IF;
    IF EXISTS (SELECT 1 FROM tenant_features WHERE tenant_id <> 'kaos-biru-konveksi'
          AND feature IN ('conversational_customer_inline', 'conversational_item_inline')) THEN
        RAISE EXCEPTION 'V403: flag master inline ditemukan di tenant lain (grapgrap wajib NOL)';
    END IF;
END $$;
