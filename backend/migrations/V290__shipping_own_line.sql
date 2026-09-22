-- V290: ONGKOS KIRIM = BARIS SENDIRI (disetujui pemilik 23 Sep, pola SaaS akuntansi umum).
-- ADITIF & idempoten. Law 19: nol jurnal disentuh.
--
-- 1. Kolom pajak ongkir di header faktur & SO: kode pajak ongkir (NULL = ikut kode pajak
--    barang), tarif, DPP, dan PPN ongkir yang dipakai. UU PPN Pasal 1 angka 18: Harga Jual
--    termasuk "semua biaya yang diminta ... oleh penjual" -> default = kode pajak barangnya;
--    bisa diubah per dokumen (mis. NONE untuk ongkir titipan). Kasus tepi = konsultan pajak.
-- 2. Pagar Law 19 faktur: tarif/PPN/DPP ongkir ikut beku setelah dibukukan.
-- 3. Peran BARU REVENUE_SHIPPING (Law 27) -> akun BARU 4-10160 "Pendapatan Ongkos Kirim"
--    di bawah 4-10000, untuk tiap tenant yang punya 4-10000 (pola V260 4-10150). Ongkir
--    TIDAK lagi disebar ke allocated_amount baris barang.
-- 4. Tenant BARU lahir dengan akun + peran yang sama (seed_default_coa /
--    seed_default_account_roles dibangun ulang dari definisi HIDUP + 1 baris masing-masing).

ALTER TABLE sales_invoices
    ADD COLUMN IF NOT EXISTS shipping_tax_code_id uuid REFERENCES tax_codes(id) ON DELETE RESTRICT,
    ADD COLUMN IF NOT EXISTS shipping_tax_rate numeric(5,2) NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS shipping_tax_amount numeric(18,2) NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS shipping_dpp numeric(18,2) NOT NULL DEFAULT 0;

ALTER TABLE sales_orders
    ADD COLUMN IF NOT EXISTS shipping_tax_code_id uuid REFERENCES tax_codes(id) ON DELETE RESTRICT,
    ADD COLUMN IF NOT EXISTS shipping_tax_rate numeric(5,2) NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS shipping_tax_amount numeric(18,2) NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS shipping_dpp numeric(18,2) NOT NULL DEFAULT 0;

DROP TRIGGER IF EXISTS trg_law19_bekukan_nominal ON sales_invoices;
CREATE TRIGGER trg_law19_bekukan_nominal
    BEFORE UPDATE ON public.sales_invoices
    FOR EACH ROW WHEN ((old.journal_id IS NOT NULL))
    EXECUTE FUNCTION law19_bekukan_nominal(
        'subtotal', 'discount_percent', 'discount_amount', 'tax_rate', 'tax_amount',
        'total_amount', 'shipping_amount', 'shipping_tax_rate', 'shipping_tax_amount',
        'shipping_dpp'
    );

-- role_key allowlist: append (pola V274; idempoten).
DO $$
DECLARE cdef text;
BEGIN
    SELECT pg_get_constraintdef(oid) INTO cdef FROM pg_constraint
     WHERE conrelid = 'account_roles'::regclass AND conname = 'account_roles_role_key_check';
    IF cdef IS NOT NULL AND position('REVENUE_SHIPPING' in cdef) = 0 THEN
        EXECUTE 'ALTER TABLE account_roles DROP CONSTRAINT account_roles_role_key_check';
        cdef := replace(cdef, ']))', ', ''REVENUE_SHIPPING''::text]))');
        EXECUTE 'ALTER TABLE account_roles ADD CONSTRAINT account_roles_role_key_check ' || cdef;
    END IF;
END $$;

-- Tenant yang ada: akun 4-10160 + peta peran (idempoten; pola V260).
INSERT INTO chart_of_accounts (tenant_id, account_code, name, account_type, normal_balance, parent_code, level, is_header)
SELECT DISTINCT c.tenant_id, '4-10160', 'Pendapatan Ongkos Kirim', 'REVENUE', 'CREDIT', '4-10000', 2, false
FROM chart_of_accounts c
WHERE c.account_code = '4-10000'
  AND NOT EXISTS (SELECT 1 FROM chart_of_accounts c2 WHERE c2.tenant_id = c.tenant_id AND c2.account_code = '4-10160');

INSERT INTO account_roles (tenant_id, role_key, account_id, notes)
SELECT coa.tenant_id, 'REVENUE_SHIPPING', coa.id, 'V290 ongkir baris sendiri.'
FROM chart_of_accounts coa
WHERE coa.account_code = '4-10160'
  AND NOT EXISTS (SELECT 1 FROM account_roles ar WHERE ar.tenant_id = coa.tenant_id AND ar.role_key = 'REVENUE_SHIPPING');

-- Tenant BARU.
CREATE OR REPLACE FUNCTION public.seed_default_coa(p_tenant_id text)
 RETURNS integer
 LANGUAGE plpgsql
AS $function$
DECLARE
    v_count INTEGER := 0;
BEGIN
    IF NOT EXISTS (SELECT 1 FROM "Tenant" WHERE id = p_tenant_id) THEN
        RAISE EXCEPTION 'Tenant not found: %', p_tenant_id;
    END IF;

    IF EXISTS (SELECT 1 FROM chart_of_accounts WHERE tenant_id = p_tenant_id LIMIT 1) THEN
        RAISE NOTICE 'CoA already exists for tenant %, skipping', p_tenant_id;
        RETURN 0;
    END IF;

    PERFORM set_config('app.tenant_id', p_tenant_id, true);

    INSERT INTO chart_of_accounts (tenant_id, account_code, name, account_type, normal_balance, parent_code, level, is_header) VALUES
        (p_tenant_id, '1-10000', 'Aset Lancar', 'ASSET', 'DEBIT', NULL, 1, true),
        (p_tenant_id, '1-10100', 'Kas', 'ASSET', 'DEBIT', '1-10000', 2, false),
        (p_tenant_id, '1-10200', 'Bank', 'ASSET', 'DEBIT', '1-10000', 2, false),
        (p_tenant_id, '1-10300', 'Kas Kecil', 'ASSET', 'DEBIT', '1-10000', 2, false),
        (p_tenant_id, '1-10400', 'Piutang Usaha', 'RECEIVABLE', 'DEBIT', '1-10000', 2, false),
        (p_tenant_id, '1-10500', 'Piutang Lain-lain', 'ASSET', 'DEBIT', '1-10000', 2, false),
        (p_tenant_id, '1-10550', 'Uang Muka Pembelian', 'ASSET', 'DEBIT', '1-10000', 2, false),
        (p_tenant_id, '1-10600', 'Persediaan Barang Dagangan', 'ASSET', 'DEBIT', '1-10000', 2, false),
        (p_tenant_id, '1-10650', 'Barang Dalam Proses (WIP)', 'ASSET', 'DEBIT', NULL, 1, false),
        (p_tenant_id, '1-10700', 'Biaya Dibayar Dimuka', 'ASSET', 'DEBIT', '1-10000', 2, false),
        (p_tenant_id, '1-10800', 'PPN Masukan', 'ASSET', 'DEBIT', '1-10000', 2, false),
        (p_tenant_id, '1-10820', 'PPh Dibayar Dimuka', 'ASSET', 'DEBIT', '1-10000', 2, false),
        (p_tenant_id, '1-20000', 'Aset Tetap', 'ASSET', 'DEBIT', NULL, 1, true),
        (p_tenant_id, '1-20100', 'Tanah', 'ASSET', 'DEBIT', '1-20000', 2, false),
        (p_tenant_id, '1-20200', 'Bangunan', 'ASSET', 'DEBIT', '1-20000', 2, false),
        (p_tenant_id, '1-20300', 'Kendaraan', 'ASSET', 'DEBIT', '1-20000', 2, false),
        (p_tenant_id, '1-20400', 'Peralatan', 'ASSET', 'DEBIT', '1-20000', 2, false),
        (p_tenant_id, '1-20900', 'Akumulasi Penyusutan', 'ASSET', 'CREDIT', '1-20000', 2, false);
    v_count := v_count + 18;

    INSERT INTO chart_of_accounts (tenant_id, account_code, name, account_type, normal_balance, parent_code, level, is_header) VALUES
        (p_tenant_id, '2-10000', 'Liabilitas Jangka Pendek', 'LIABILITY', 'CREDIT', NULL, 1, true),
        (p_tenant_id, '2-10100', 'Hutang Usaha', 'PAYABLE', 'CREDIT', '2-10000', 2, false),
        (p_tenant_id, '2-10200', 'Hutang Lain-lain', 'LIABILITY', 'CREDIT', '2-10000', 2, false),
        (p_tenant_id, '2-10300', 'Hutang Pajak', 'LIABILITY', 'CREDIT', '2-10000', 2, false),
        (p_tenant_id, '2-10310', 'Utang PPh 21', 'LIABILITY', 'CREDIT', NULL, 1, false),
        (p_tenant_id, '2-10320', 'Hutang PPh Transaksi', 'LIABILITY', 'CREDIT', '2-10000', 2, false),
        (p_tenant_id, '2-10400', 'Hutang Gaji', 'LIABILITY', 'CREDIT', '2-10000', 2, false),
        (p_tenant_id, '2-10410', 'Utang BPJS Karyawan', 'LIABILITY', 'CREDIT', NULL, 1, false),
        (p_tenant_id, '2-10420', 'Utang BPJS Perusahaan', 'LIABILITY', 'CREDIT', NULL, 1, false),
        (p_tenant_id, '2-10430', 'Hutang TKL Applied', 'LIABILITY', 'CREDIT', NULL, 1, false),
        (p_tenant_id, '2-10440', 'Hutang Overhead Applied', 'LIABILITY', 'CREDIT', NULL, 1, false),
        (p_tenant_id, '2-10500', 'Uang Muka Pelanggan', 'LIABILITY', 'CREDIT', '2-10000', 2, false),
        (p_tenant_id, '2-10600', 'PPN Keluaran', 'LIABILITY', 'CREDIT', '2-10000', 2, false),
        (p_tenant_id, '2-10750', 'Pendapatan Diterima Dimuka', 'LIABILITY', 'CREDIT', '2-10000', 2, false),
        (p_tenant_id, '2-20000', 'Liabilitas Jangka Panjang', 'LIABILITY', 'CREDIT', NULL, 1, true),
        (p_tenant_id, '2-20100', 'Hutang Bank', 'LIABILITY', 'CREDIT', '2-20000', 2, false);
    v_count := v_count + 16;

    INSERT INTO chart_of_accounts (tenant_id, account_code, name, account_type, normal_balance, parent_code, level, is_header) VALUES
        (p_tenant_id, '3-10000', 'Modal', 'EQUITY', 'CREDIT', NULL, 1, true),
        (p_tenant_id, '3-10100', 'Modal Pemilik', 'EQUITY', 'CREDIT', '3-10000', 2, false),
        (p_tenant_id, '3-20000', 'Laba Ditahan', 'EQUITY', 'CREDIT', NULL, 1, false),
        (p_tenant_id, '3-30000', 'Laba Tahun Berjalan', 'EQUITY', 'CREDIT', NULL, 1, false),
        (p_tenant_id, '3-40000', 'Prive', 'EQUITY', 'DEBIT', NULL, 1, false),
        (p_tenant_id, '3-50000', 'Modal Saldo Awal', 'EQUITY', 'CREDIT', '3-10000', 2, false);
    v_count := v_count + 6;

    INSERT INTO chart_of_accounts (tenant_id, account_code, name, account_type, normal_balance, parent_code, level, is_header) VALUES
        (p_tenant_id, '4-10000', 'Pendapatan Usaha', 'REVENUE', 'CREDIT', NULL, 1, true),
        (p_tenant_id, '4-10100', 'Penjualan', 'REVENUE', 'CREDIT', '4-10000', 2, false),
        (p_tenant_id, '4-10150', 'Pendapatan Jasa', 'REVENUE', 'CREDIT', '4-10000', 2, false),
        (p_tenant_id, '4-10160', 'Pendapatan Ongkos Kirim', 'REVENUE', 'CREDIT', '4-10000', 2, false),
        (p_tenant_id, '4-10200', 'Diskon Penjualan', 'REVENUE', 'DEBIT', '4-10000', 2, false),
        (p_tenant_id, '4-10300', 'Retur Penjualan', 'REVENUE', 'DEBIT', '4-10000', 2, false),
        (p_tenant_id, '4-90000', 'Pendapatan Lain-lain', 'OTHER_INCOME', 'CREDIT', NULL, 1, false);
    v_count := v_count + 7;

    INSERT INTO chart_of_accounts (tenant_id, account_code, name, account_type, normal_balance, parent_code, level, is_header, is_system) VALUES
        (p_tenant_id, '5-10000', 'Harga Pokok Penjualan', 'COGS', 'DEBIT', NULL, 1, true, false),
        (p_tenant_id, '5-10100', 'HPP - Pembelian Barang', 'COGS', 'DEBIT', '5-10000', 2, false, false),
        -- V183: COGS_SERVICE account — service-purchase classification (Beban Pokok Jasa)
        (p_tenant_id, '5-10110', 'Beban Pokok Jasa', 'COGS', 'DEBIT', '5-10000', 2, false, false),
        (p_tenant_id, '5-10200', 'Diskon Pembelian', 'COGS', 'CREDIT', '5-10000', 2, false, false),
        (p_tenant_id, '5-10300', 'Retur Pembelian', 'COGS', 'CREDIT', '5-10000', 2, false, false),
        (p_tenant_id, '5-20000', 'Beban Operasional', 'EXPENSE', 'DEBIT', NULL, 1, true, false),
        (p_tenant_id, '5-20100', 'Beban Gaji', 'EXPENSE', 'DEBIT', '5-20000', 2, false, false),
        (p_tenant_id, '5-20150', 'Beban BPJS Perusahaan', 'EXPENSE', 'DEBIT', NULL, 1, false, false),
        (p_tenant_id, '5-20200', 'Beban Sewa', 'EXPENSE', 'DEBIT', '5-20000', 2, false, false),
        (p_tenant_id, '5-20300', 'Beban Listrik & Air', 'EXPENSE', 'DEBIT', '5-20000', 2, false, false),
        (p_tenant_id, '5-20400', 'Beban Telepon & Internet', 'EXPENSE', 'DEBIT', '5-20000', 2, false, false),
        (p_tenant_id, '5-20500', 'Beban Transportasi', 'EXPENSE', 'DEBIT', '5-20000', 2, false, false),
        (p_tenant_id, '5-20600', 'Beban Perlengkapan', 'EXPENSE', 'DEBIT', '5-20000', 2, false, false),
        (p_tenant_id, '5-20700', 'Beban Pemeliharaan', 'EXPENSE', 'DEBIT', '5-20000', 2, false, false),
        (p_tenant_id, '5-20800', 'Beban Administrasi', 'EXPENSE', 'DEBIT', '5-20000', 2, false, false),
        (p_tenant_id, '5-20850', 'Biaya Administrasi Bank', 'EXPENSE', 'DEBIT', '5-20000', 2, false, false),
        (p_tenant_id, '5-20900', 'Beban Lain-lain', 'EXPENSE', 'DEBIT', '5-20000', 2, false, false),
        (p_tenant_id, '5-30000', 'Beban Penyusutan', 'EXPENSE', 'DEBIT', NULL, 1, true, false),
        (p_tenant_id, '5-30100', 'Beban Penyusutan Bangunan', 'EXPENSE', 'DEBIT', '5-30000', 2, false, false),
        (p_tenant_id, '5-30200', 'Beban Penyusutan Kendaraan', 'EXPENSE', 'DEBIT', '5-30000', 2, false, false),
        (p_tenant_id, '5-30300', 'Beban Penyusutan Peralatan', 'EXPENSE', 'DEBIT', '5-30000', 2, false, false),
        (p_tenant_id, '5-50100', 'Biaya Penyesuaian Persediaan', 'EXPENSE', 'DEBIT', NULL, 2, false, true),
        (p_tenant_id, '5-80000', 'Beban Pajak', 'EXPENSE', 'DEBIT', NULL, 1, false, false),
        (p_tenant_id, '5-80100', 'Beban PPh 21 Perusahaan', 'EXPENSE', 'DEBIT', NULL, 1, false, false),
        (p_tenant_id, '5-90000', 'Beban Non-Operasional', 'OTHER_EXPENSE', 'DEBIT', NULL, 1, false, false),
        (p_tenant_id, '5-90200', 'Selisih Produksi', 'OTHER_EXPENSE', 'DEBIT', '5-90000', 3, false, false);
    v_count := v_count + 25;

    RETURN v_count;
END;
$function$;

CREATE OR REPLACE FUNCTION public.seed_default_account_roles(p_tenant_id text)
 RETURNS integer
 LANGUAGE plpgsql
AS $function$
DECLARE
    v_inserted INTEGER := 0;
    v_account_id UUID;
    v_is_header  BOOLEAN;
    v_map RECORD;
BEGIN
    FOR v_map IN
        SELECT * FROM (VALUES
            ('CASH_GENERAL',               '1-10100', false, NULL::TEXT),
            ('BANK_OPERATIONAL',           '1-10200', false, NULL),
            ('AR_TRADE',                   '1-10400', false, NULL),
            ('AR_OTHER',                   '1-10500', false, NULL),
            ('INVENTORY_MERCHANDISE',      '1-10600', false, NULL),
            ('AP_TRADE',                   '2-10100', false, NULL),
            ('CUSTOMER_DEPOSIT_LIABILITY', '2-10500', false, NULL),
            ('REVENUE_DEFERRED',           '2-10750', false, 'V152 promote.'),
            ('EQUITY_OPENING_BALANCE',     '3-50000', false, NULL),
            ('REVENUE_SALES_GOODS',        '4-10100', false, NULL),
            ('REVENUE_SALES_RETURN',       '4-10300', false, NULL),
            ('COGS_SALES',                 '5-10100', false, NULL),
            ('COGS_PURCHASE_RETURN',       '5-10300', false, NULL),
            ('CASH_PETTY',                 '1-10300', false, 'Kas Kecil.'),
            ('BANK_FEE',                   '5-20850', false, 'V165 dipulihkan V198.'),
            ('VAT_OUTPUT',                 '2-10600', false, 'V155 D1.'),
            ('VAT_INPUT',                  '1-10800', false, 'V155 D1.'),
            ('WHT_PPH_PAYABLE',            '2-10320', false, 'V155 D1.'),
            ('WHT_PPH_PREPAID',            '1-10820', false, 'V155 D1.'),
            ('AP_PREPAID',                 '1-10550', false, 'V156 D2-wrap B.'),
            ('PURCHASE_DISCOUNT',          '5-10200', false, 'V156 D2-wrap B.'),
            ('REVENUE_SALES_DISCOUNT',     '4-10200', false, 'V157 D2-wrap D.'),
            ('REVENUE_SHIPPING',           '4-10160', false, 'V290 ongkir baris sendiri.'),
            ('WIP_GENERIC',                '1-10650', false, 'V159 D3.2.'),
            ('COGS_VARIANCE_PRODUCTION',   '5-90200', false, 'V159 D3.2.'),
            ('WIP_SUBCONTRACT',            '1-10650', false, 'V159 D3.2.'),
            ('INVENTORY_ADJUSTMENT_EXPENSE','5-50100', false, 'V159 D3.2.'),
            ('SALARY_EXPENSE',             '5-20100', false, 'V162 D4.2.'),
            ('SALARY_PAYABLE',             '2-10400', false, 'V162 D4.2.'),
            ('PPH21_PAYABLE',              '2-10310', false, 'V162 D4.2.'),
            ('BPJS_EE_PAYABLE',            '2-10410', false, 'V162 D4.2.'),
            ('BPJS_ER_PAYABLE',            '2-10420', false, 'V162 D4.2.'),
            ('BPJS_ER_EXPENSE',            '5-20150', false, 'V162 D4.2.'),
            ('PPH21_ER_EXPENSE',           '5-80100', false, 'V162 D4.2.'),
            ('MFG_LABOR_APPLIED',          '2-10430', false, 'V173 deep-val 2.5: Hutang TKL Applied — credit at labor-time (standard cost) absorbed into WIP; cleared by payroll settlement journal (Dr 2-10430 / Cr 5-20100 actual).'),
            ('MFG_OVERHEAD_APPLIED',       '2-10440', false, 'V173 deep-val 2.5: Hutang Overhead Applied — credit at labor-time (auto-applied via work_centers.overhead_rate_per_hour) absorbed into WIP; cleared by OH actuals.'),
            ('MFG_DIRECT_LABOR',           '5-20100', false, 'V173 deep-val 2.5: reuse Beban Gaji (settle path — payroll posting clears MFG_LABOR_APPLIED).'),
            ('MFG_ACTUAL_OVERHEAD',        '5-30300', false, 'V174: actual production OH basis = Penyusutan peralatan jahit (pilot decision; Sewa/Listrik excluded — cannot split factory vs office from ledger).'),
            -- V183 — service-purchase COGS classification
            ('COGS_SERVICE',               '5-10110', false, 'V183: Beban Pokok Jasa — preferred debit for service / non-tracked bill lines in create_bill_v2 (graceful fallback to COGS_SALES for tenants lacking this mapping).')
        ) AS t(role_key, account_code, is_interim, notes)
    LOOP
        SELECT id, is_header INTO v_account_id, v_is_header
          FROM chart_of_accounts
         WHERE tenant_id = p_tenant_id
           AND account_code = v_map.account_code;

        IF v_account_id IS NULL THEN
            RAISE NOTICE 'seed_default_account_roles[%]: skipping role % — account_code % not found',
                p_tenant_id, v_map.role_key, v_map.account_code;
            CONTINUE;
        END IF;

        IF v_is_header THEN
            RAISE NOTICE 'seed_default_account_roles[%]: skipping role % — account_code % is_header=true (Law 18)',
                p_tenant_id, v_map.role_key, v_map.account_code;
            CONTINUE;
        END IF;

        INSERT INTO account_roles (tenant_id, role_key, account_id, is_interim, notes)
        VALUES (p_tenant_id, v_map.role_key, v_account_id, v_map.is_interim, v_map.notes)
        ON CONFLICT (tenant_id, role_key) DO NOTHING;

        IF FOUND THEN
            v_inserted := v_inserted + 1;
        END IF;
    END LOOP;

    RETURN v_inserted;
END;
$function$;
