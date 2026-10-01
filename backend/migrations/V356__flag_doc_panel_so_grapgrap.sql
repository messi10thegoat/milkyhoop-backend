-- V356 (1 Okt 2026, perintah pemilik lewat MASTER "panel dokumen grapgrap ON sekarang tanpa Kirim" + aturan tetap
-- pemilik 30 Sep: flag CW yang lulus gerbang + uji nyata kaos boleh dinyalakan di grapgrap) -- flag
-- `conversational_doc_panel_so` (kolom Posisi · Kirim · Dok. + panel dokumen di daftar Pesanan) untuk GRAPGRAP.
-- Bukti: gerbang 05 P6 D1-D7 lulus di data grapgrap baca-saja (BACKEND) + gerbang FE r194 + uji nyata kaos (WORKSPACE).
-- Kirim (P5) TETAP mati untuk grapgrap: FE r194 tak punya tombol Kirim.
--
-- Dibaca GET /api/permissions/me -> `features` -> nol perubahan kode, nol restart. Idempoten; gagal keras bila tak
-- mendarat ATAU tenant selain kaos/grapgrap ikut memilikinya.

INSERT INTO tenant_features (tenant_id, feature)
SELECT 'grapgrap-manado', 'conversational_doc_panel_so'
WHERE EXISTS (SELECT 1 FROM "Tenant" WHERE id = 'grapgrap-manado')
ON CONFLICT (tenant_id, feature) DO NOTHING;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM tenant_features
                   WHERE tenant_id = 'grapgrap-manado' AND feature = 'conversational_doc_panel_so' AND enabled) THEN
        RAISE EXCEPTION 'V356: flag conversational_doc_panel_so grapgrap-manado tidak mendarat/aktif';
    END IF;
    IF EXISTS (SELECT 1 FROM tenant_features
               WHERE tenant_id NOT IN ('kaos-biru-konveksi', 'grapgrap-manado') AND feature = 'conversational_doc_panel_so') THEN
        RAISE EXCEPTION 'V356: flag conversational_doc_panel_so ditemukan di tenant lain';
    END IF;
END $$;
