-- V404 (MASTER 11 Okt 2026): flag master inline (Penawaran) untuk GRAPGRAP — izin pemilik LANGSUNG
-- ("ok, nyalakan grapgrap", 11 Okt, sesudah r227 LIVE 85a9a92e5 + gerbang + nyata kaos hijau).
-- conversational_customer_inline (F2 panel Pelanggan) + conversational_item_inline (F3 panel Barang).
-- Cakupan = form Penawaran saja (FE r227: R.key==='quote'); SO/Faktur (F5) memakai flag cakupan TERPISAH.
-- Idempoten; gagal keras bila tak mendarat ATAU tenant selain kaos/grapgrap ikut memilikinya.

INSERT INTO tenant_features (tenant_id, feature)
SELECT 'grapgrap-manado', f
FROM unnest(ARRAY['conversational_customer_inline', 'conversational_item_inline']) AS f
WHERE EXISTS (SELECT 1 FROM "Tenant" WHERE id = 'grapgrap-manado')
ON CONFLICT (tenant_id, feature) DO NOTHING;

DO $$
BEGIN
    IF (SELECT count(*) FROM tenant_features WHERE tenant_id = 'grapgrap-manado' AND enabled
          AND feature IN ('conversational_customer_inline', 'conversational_item_inline')) <> 2 THEN
        RAISE EXCEPTION 'V404: flag master inline grapgrap tidak mendarat/aktif (harus 2)';
    END IF;
    IF EXISTS (SELECT 1 FROM tenant_features WHERE tenant_id NOT IN ('kaos-biru-konveksi', 'grapgrap-manado')
          AND feature IN ('conversational_customer_inline', 'conversational_item_inline')) THEN
        RAISE EXCEPTION 'V404: flag master inline ditemukan di tenant selain kaos/grapgrap';
    END IF;
END $$;
