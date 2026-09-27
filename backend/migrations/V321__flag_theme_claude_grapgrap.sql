-- V321 (pemilik 27 Sep 2026 ~19:10 WIB lewat MASTER; dikonfirmasi pengguna "gas flag theme_claude untuk grapgrap")
-- flag theme_claude untuk grapgrap-manado (kaos sudah sejak V319). Belum ada kode FE yang membaca flag ini: tanpa efek
-- tampak sampai rilis tema (butuh izin deploy pemilik tersendiri).
-- Pola V319: idempoten (ON CONFLICT: baris lama enabled=false ikut dinyalakan), gagal keras bila tak mendarat/aktif.

INSERT INTO tenant_features (tenant_id, feature)
SELECT 'grapgrap-manado', 'theme_claude'
WHERE EXISTS (SELECT 1 FROM "Tenant" WHERE id = 'grapgrap-manado')
ON CONFLICT (tenant_id, feature) DO UPDATE SET enabled = true, updated_at = now();

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM tenant_features
                   WHERE tenant_id = 'grapgrap-manado' AND feature = 'theme_claude' AND enabled) THEN
        RAISE EXCEPTION 'V321: flag theme_claude grapgrap-manado tidak mendarat/aktif';
    END IF;
END $$;
