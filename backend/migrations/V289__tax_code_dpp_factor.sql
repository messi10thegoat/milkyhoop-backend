-- V289: faktor DPP PER KODE PAJAK (PMK 131/2024). ADITIF.
--
-- 12% atas DPP nilai lain 11/12 = kasus umum; 12% atas DPP penuh = hanya barang mewah
-- (PPnBM); 11% langsung tetap dipakai dokumen lama / lawan transaksi. ATURAN PEMILIK
-- 23 Sep: kode PPN-11-* DAN PPN-12-* dipertahankan -- tidak dihapus, digabung, dimigrasi.
-- Faktor disimpan sebagai PECAHAN EKSAK (pembilang/penyebut): 11/12 tak terwakili tepat
-- sebagai desimal, dan pembulatannya menggeser sen pada nilai besar.
--
-- SEED NETRAL = angka hari ini TIDAK berubah:
--   * Tagihan (arah input) hari ini memakai 11/12 bila tarif header 12 (bills_service,
--     `if tax_rate == 12`) -> PPN-12-IN = 11/12.
--   * Faktur/SO (arah output) hari ini memakai DPP penuh -> PPN-12-OUT = 1/1.
--   * PPN-11-* = 1/1.
-- Apakah PPN-12-OUT seharusnya 11/12 = KEPUTUSAN PEMILIK (lihat laporan), bukan migrasi ini.
-- Terukur 23 Sep: 0 dokumen memakai tarif 12% di tenant mana pun.

ALTER TABLE tax_codes
    ADD COLUMN IF NOT EXISTS dpp_factor_num integer NOT NULL DEFAULT 1,
    ADD COLUMN IF NOT EXISTS dpp_factor_den integer NOT NULL DEFAULT 1;

DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'tax_codes_dpp_factor_chk') THEN
        ALTER TABLE tax_codes ADD CONSTRAINT tax_codes_dpp_factor_chk
            CHECK (dpp_factor_num > 0 AND dpp_factor_den > 0 AND dpp_factor_num <= dpp_factor_den);
    END IF;
END $$;

UPDATE tax_codes SET dpp_factor_num = 11, dpp_factor_den = 12
WHERE code = 'PPN-12-IN' AND dpp_factor_num = 1 AND dpp_factor_den = 1;

-- Tenant BARU wajib lahir dengan seed yang sama (tanpa ini, tagihan 12% tenant baru
-- berubah dari 11/12 menjadi DPP penuh saat hardcode diganti faktor).
CREATE OR REPLACE FUNCTION public.seed_default_tax_codes(p_tenant_id character varying)
 RETURNS void
 LANGUAGE plpgsql
AS $function$
DECLARE
    v_ppn_masukan_id  UUID;
    v_ppn_keluaran_id UUID;
    v_utang_pajak_id  UUID;
BEGIN
    SELECT id INTO v_ppn_masukan_id
    FROM chart_of_accounts WHERE tenant_id = p_tenant_id AND account_code = '1-10800';

    SELECT id INTO v_ppn_keluaran_id
    FROM chart_of_accounts WHERE tenant_id = p_tenant_id AND account_code = '2-10600';

    SELECT id INTO v_utang_pajak_id
    FROM chart_of_accounts WHERE tenant_id = p_tenant_id AND account_code = '2-10300';

    IF v_ppn_masukan_id IS NULL OR v_ppn_keluaran_id IS NULL OR v_utang_pajak_id IS NULL THEN
        RAISE NOTICE 'Skipping tax seed for tenant % — missing CoA accounts', p_tenant_id;
        RETURN;
    END IF;

    -- PPN rows: direction REQUIRED. PPh + 'none': direction NULL (load-bearing).
    -- dpp_factor: PPN-12-IN 11/12 (perilaku tagihan sebelum V289), lainnya 1/1.
    INSERT INTO tax_codes (tenant_id, code, name, rate, tax_type, direction, is_withholding, coa_id, is_default, coretax_tax_code, dpp_factor_num, dpp_factor_den) VALUES
        (p_tenant_id, 'PPN-11-OUT', 'PPN 11% Keluaran',  11.00, 'ppn',    'output', FALSE, v_ppn_keluaran_id, FALSE, '04', 1, 1),
        (p_tenant_id, 'PPN-12-OUT', 'PPN 12% Keluaran',  12.00, 'ppn',    'output', FALSE, v_ppn_keluaran_id, TRUE,  '04', 1, 1),
        (p_tenant_id, 'PPN-11-IN',  'PPN 11% Masukan',   11.00, 'ppn',    'input',  FALSE, v_ppn_masukan_id,  FALSE, '04', 1, 1),
        (p_tenant_id, 'PPN-12-IN',  'PPN 12% Masukan',   12.00, 'ppn',    'input',  FALSE, v_ppn_masukan_id,  FALSE, '04', 11, 12),
        (p_tenant_id, 'PPH21',      'PPh 21',             5.00, 'pph21',  NULL,     TRUE,  v_utang_pajak_id,  FALSE, NULL, 1, 1),
        (p_tenant_id, 'PPH23-2',    'PPh 23 - Jasa',      2.00, 'pph23',  NULL,     TRUE,  v_utang_pajak_id,  FALSE, NULL, 1, 1),
        (p_tenant_id, 'PPH23-15',   'PPh 23 - Dividen',  15.00, 'pph23',  NULL,     TRUE,  v_utang_pajak_id,  FALSE, NULL, 1, 1),
        (p_tenant_id, 'PPH4_2',     'PPh 4(2) Final',     0.50, 'pph4_2', NULL,     TRUE,  v_utang_pajak_id,  FALSE, NULL, 1, 1),
        (p_tenant_id, 'NONE',       'Tanpa Pajak',        0.00, 'none',   NULL,     FALSE, NULL,              FALSE, NULL, 1, 1)
    ON CONFLICT (tenant_id, code) DO NOTHING;
END;
$function$;
