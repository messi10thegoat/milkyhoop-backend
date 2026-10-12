-- V417 (BACKEND2 12 Okt 2026; R1 IRISAN 2, putusan pemilik P1 Barang Terkirim + P2 pendapatan saat faktur) -- JALANKAN dengan:
--   psql -v ON_ERROR_STOP=1 -1   (satu transaksi)
-- ATURAN BUKU BARU (izin LANGSUNG pemilik): SJ dari pesanan untuk barang BER-STOK:
--   saat kirim   Dr Barang Terkirim (1-10660) / Cr Persediaan (INVENTORY_MERCHANDISE)   nilai = qty x WAC snapshot
--   saat faktur  Dr HPP (COGS_SALES) / Cr Barang Terkirim (snapshot SJ)  + INVOICE_REVENUE Dr Dimuka / Cr Penjualan (qty teralokasi)
-- Law 18 (akun baru = struktur CoA), 27 (role runtime), 6 (source_type terdaftar, FK-enforced), 2/26 (pembalik).
--   1. sj_faktur_alokasi: + total_cost (snapshot), + cogs_journal_id, + revenue_journal_id (INSERT-only tetap).
--   2. Peran GOODS_DELIVERED_NOT_INVOICED: CHECK account_roles dibangun dari definisi sekarang (tak ada nilai terbuang),
--      akun 1-10660 "Barang Terkirim" (salin atribut 1-10600) + peran per tenant, seed_default_coa/seed_default_account_roles
--      ditambal (tenant baru).
--   3. journal_source_types: DELIVERY_NOTE, DELIVERY_NOTE_REVERSAL, DELIVERY_INVOICED, DELIVERY_INVOICED_REVERSAL.
--   4. is_effective_journal: daftar hitam pembalik memuat dua pembalik baru.
ALTER TABLE sj_faktur_alokasi ADD COLUMN IF NOT EXISTS total_cost numeric NOT NULL DEFAULT 0;
ALTER TABLE sj_faktur_alokasi ADD COLUMN IF NOT EXISTS cogs_journal_id uuid REFERENCES journal_entries(id);
ALTER TABLE sj_faktur_alokasi ADD COLUMN IF NOT EXISTS revenue_journal_id uuid REFERENCES journal_entries(id);

-- 2a. CHECK role_key (bangun dari definisi SEKARANG)
DO $$
DECLARE v_def text; v_baru text;
BEGIN
    SELECT pg_get_constraintdef(oid) INTO v_def FROM pg_constraint WHERE conname = 'account_roles_role_key_check';
    IF v_def IS NULL THEN RAISE EXCEPTION 'V417: account_roles_role_key_check tidak ditemukan'; END IF;
    IF v_def NOT LIKE '%ARRAY[%' THEN RAISE EXCEPTION 'V417: bentuk CHECK tak dikenal: %', left(v_def, 120); END IF;
    IF v_def NOT LIKE '%GOODS_DELIVERED_NOT_INVOICED%' THEN
        v_baru := replace(v_def, 'ARRAY[', 'ARRAY[''GOODS_DELIVERED_NOT_INVOICED''::text, ');
        EXECUTE 'ALTER TABLE account_roles DROP CONSTRAINT account_roles_role_key_check';
        EXECUTE 'ALTER TABLE account_roles ADD CONSTRAINT account_roles_role_key_check ' || v_baru;
    END IF;
    IF (SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname = 'account_roles_role_key_check') NOT LIKE '%GOODS_DELIVERED_NOT_INVOICED%'
       OR (SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname = 'account_roles_role_key_check') NOT LIKE '%INVENTORY_MERCHANDISE%' THEN
        RAISE EXCEPTION 'V417: CHECK role_key tidak memuat nilai lama + baru';
    END IF;
END $$;

-- 2b. akun 1-10660 per tenant (atribut disalin dari 1-10600), dengan penjaga tabrakan
DO $$
DECLARE n_tabrakan int;
BEGIN
    SELECT count(*) INTO n_tabrakan FROM chart_of_accounts WHERE account_code = '1-10660' AND name <> 'Barang Terkirim';
    IF n_tabrakan > 0 THEN RAISE EXCEPTION 'V417: kode 1-10660 dipakai akun lain di % tenant', n_tabrakan; END IF;
END $$;
INSERT INTO chart_of_accounts (tenant_id, account_code, name, account_type, normal_balance, parent_code, level, is_header,
                               is_detail, is_system, is_cash, is_bank_account, cash_flow_category, psak_sub_category, category, is_active)
SELECT c.tenant_id, '1-10660', 'Barang Terkirim', c.account_type, c.normal_balance, c.parent_code, c.level, false,
       c.is_detail, false, false, false, c.cash_flow_category, c.psak_sub_category, c.category, true
FROM chart_of_accounts c
WHERE c.account_code = '1-10600'
  AND NOT EXISTS (SELECT 1 FROM chart_of_accounts x WHERE x.tenant_id = c.tenant_id AND x.account_code = '1-10660');

INSERT INTO account_roles (tenant_id, role_key, account_id, is_interim, notes)
SELECT a.tenant_id, 'GOODS_DELIVERED_NOT_INVOICED', a.id, false, 'V417 R1 irisan 2: Barang Terkirim (kirim sebelum faktur).'
FROM chart_of_accounts a
WHERE a.account_code = '1-10660'
ON CONFLICT (tenant_id, role_key) DO NOTHING;

-- 2c. seed tenant BARU (anchor; gagal-keras bila anchor hilang)
DO $$
DECLARE v_def text; v_baru text;
BEGIN
    v_def := pg_get_functiondef('seed_default_coa(text)'::regprocedure);
    IF v_def NOT LIKE '%1-10660%' THEN
        v_baru := replace(v_def, E'(p_tenant_id, ''1-10650'', ''Barang Dalam Proses (WIP)'', ''ASSET'', ''DEBIT'', NULL, 1, false),',
                          E'(p_tenant_id, ''1-10650'', ''Barang Dalam Proses (WIP)'', ''ASSET'', ''DEBIT'', NULL, 1, false),\n        (p_tenant_id, ''1-10660'', ''Barang Terkirim'', ''ASSET'', ''DEBIT'', ''1-10000'', 2, false),');
        IF v_baru = v_def THEN RAISE EXCEPTION 'V417: anchor 1-10650 tak ditemukan di seed_default_coa'; END IF;
        v_baru := replace(v_baru, 'v_count := v_count + 18;', 'v_count := v_count + 19;');
        EXECUTE v_baru;
    END IF;
    v_def := pg_get_functiondef('seed_default_account_roles(text)'::regprocedure);
    IF v_def NOT LIKE '%GOODS_DELIVERED_NOT_INVOICED%' THEN
        v_baru := replace(v_def, E'(''INVENTORY_MERCHANDISE'',      ''1-10600'', false, NULL),',
                          E'(''INVENTORY_MERCHANDISE'',      ''1-10600'', false, NULL),\n            (''GOODS_DELIVERED_NOT_INVOICED'', ''1-10660'', false, ''V417 Barang Terkirim.''),');
        IF v_baru = v_def THEN RAISE EXCEPTION 'V417: anchor INVENTORY_MERCHANDISE tak ditemukan di seed_default_account_roles'; END IF;
        EXECUTE v_baru;
    END IF;
END $$;

-- 3. source_type jurnal (FK-enforced)
INSERT INTO journal_source_types (source_type, description, is_active) VALUES
    ('DELIVERY_NOTE', 'Surat Jalan dari pesanan, barang ber-stok keluar (Dr Barang Terkirim / Cr Persediaan)', true),
    ('DELIVERY_NOTE_REVERSAL', 'Pembalik Surat Jalan dari pesanan (void mandiri)', true),
    ('DELIVERY_INVOICED', 'Reklas HPP saat faktur menagih barang terkirim (Dr HPP / Cr Barang Terkirim)', true),
    ('DELIVERY_INVOICED_REVERSAL', 'Pembalik reklas HPP saat faktur di-void', true)
ON CONFLICT (source_type) DO NOTHING;

-- 4. is_effective_journal: daftar hitam pembalik
DO $$
DECLARE v_def text; v_baru text;
BEGIN
    v_def := pg_get_functiondef('is_effective_journal(uuid)'::regprocedure);
    IF v_def NOT LIKE '%DELIVERY_NOTE_REVERSAL%' THEN
        v_baru := replace(v_def, '''SALES_INVOICE_COGS_REVERSAL''', '''SALES_INVOICE_COGS_REVERSAL'', ''DELIVERY_NOTE_REVERSAL'', ''DELIVERY_INVOICED_REVERSAL''');
        IF v_baru = v_def THEN RAISE EXCEPTION 'V417: anchor SALES_INVOICE_COGS_REVERSAL tak ditemukan di is_effective_journal'; END IF;
        EXECUTE v_baru;
    END IF;
END $$;

DO $$
DECLARE n_tenant int; n_akun int; n_peran int;
BEGIN
    SELECT count(DISTINCT tenant_id) INTO n_tenant FROM chart_of_accounts WHERE account_code = '1-10600';
    SELECT count(*) INTO n_akun FROM chart_of_accounts WHERE account_code = '1-10660' AND name = 'Barang Terkirim' AND NOT is_header;
    SELECT count(*) INTO n_peran FROM account_roles WHERE role_key = 'GOODS_DELIVERED_NOT_INVOICED';
    IF n_akun <> n_tenant OR n_peran <> n_tenant THEN
        RAISE EXCEPTION 'V417: akun %/peran % tidak sama dengan jumlah tenant ber-1-10600 %', n_akun, n_peran, n_tenant;
    END IF;
    IF (SELECT count(*) FROM journal_source_types WHERE source_type IN ('DELIVERY_NOTE','DELIVERY_NOTE_REVERSAL','DELIVERY_INVOICED','DELIVERY_INVOICED_REVERSAL')) <> 4 THEN
        RAISE EXCEPTION 'V417: source_type belum 4';
    END IF;
END $$;
