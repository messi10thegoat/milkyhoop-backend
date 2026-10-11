-- V409 (MASTER 11 Okt 2026 ~22 WIB) -- flag CW untuk GRAPGRAP, putusan pemilik LANGSUNG ("nyalakan saja di tenant grapgrap
-- kalau di kaos sudah solid"; aturan tetap flag CW -> grapgrap):
--   cf_submode                          sub-mode Master Data di form CW Penawaran (kaos V406; FE r230/r232/r233, nyata kaos lulus)
--   conversational_master_inline_so_si  panel Pelanggan/Barang JUGA di form CW SO & Faktur (kaos V405; FE r229, nyata kaos F5 lulus)
-- BUKAN pengiriman_serah (FE belum live) -- dijaga di bawah. Idempoten; gagal keras bila tak mendarat, kaos tak lagi ON,
-- flag muncul di tenant selain kaos/grapgrap, atau pengiriman_serah ikut ke grapgrap. Flag dibaca per permintaan
-- (services/tenant_features.fitur_aktif lewat GET /api/permissions/me), tanpa cache BE -> tanpa restart.
INSERT INTO tenant_features (tenant_id, feature)
SELECT 'grapgrap-manado', f
FROM unnest(ARRAY['cf_submode', 'conversational_master_inline_so_si']) AS f
WHERE EXISTS (SELECT 1 FROM "Tenant" WHERE id = 'grapgrap-manado')
ON CONFLICT (tenant_id, feature) DO NOTHING;

DO $$
BEGIN
    IF (SELECT count(*) FROM tenant_features WHERE tenant_id = 'grapgrap-manado' AND enabled
          AND feature IN ('cf_submode', 'conversational_master_inline_so_si')) <> 2 THEN
        RAISE EXCEPTION 'V409: flag cf_submode/conversational_master_inline_so_si grapgrap tidak mendarat/aktif (harus 2)';
    END IF;
    IF (SELECT count(*) FROM tenant_features WHERE tenant_id = 'kaos-biru-konveksi' AND enabled
          AND feature IN ('cf_submode', 'conversational_master_inline_so_si')) <> 2 THEN
        RAISE EXCEPTION 'V409: kaos tidak lagi ON untuk kedua flag (harus 2)';
    END IF;
    IF EXISTS (SELECT 1 FROM tenant_features WHERE tenant_id NOT IN ('kaos-biru-konveksi', 'grapgrap-manado')
          AND feature IN ('cf_submode', 'conversational_master_inline_so_si')) THEN
        RAISE EXCEPTION 'V409: flag ditemukan di tenant selain kaos/grapgrap';
    END IF;
    IF EXISTS (SELECT 1 FROM tenant_features WHERE tenant_id = 'grapgrap-manado' AND feature = 'pengiriman_serah') THEN
        RAISE EXCEPTION 'V409: pengiriman_serah ada di grapgrap (FE belum live; wajib NOL)';
    END IF;
END $$;
