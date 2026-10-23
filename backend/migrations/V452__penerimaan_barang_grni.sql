-- V452 (BACKEND2 22 Okt 2026; PEMBELIAN-SERAGAM T3 Penerimaan Barang / GRN) -- JALANKAN dengan psql -v ON_ERROR_STOP=1 -1 (apply_mig.sh).
-- PUTUSAN PEMILIK LANGSUNG 22 Okt (RANCANGAN-penerimaan-barang-grn-20261022.md §6):
--   P1 peran GOODS_RECEIVED_NOT_INVOICED + akun 2-10150 "Barang Diterima Belum Ditagih" (LIABILITY, BUKAN PAYABLE) di SEMUA tenant.
--      Terima barang ber-stok: Dr Persediaan / Cr 2-10150. Tagihan dari PO: Dr 2-10150 / Cr Hutang Usaha.
--   P3 peran PURCHASE_PRICE_VARIANCE + akun 5-10400 "Selisih Harga Pembelian" (COGS, DEBIT) -- selisih harga tagihan vs harga terima;
--      v1 tanpa revaluasi Persediaan/HPP.
-- MIGRASI-SAJA (expand): tabel dokumen + peran/akun + source_type. Kode GRN menyusul DI BALIK FLAG penerimaan_barang_v1 (P6);
-- selama kode belum ada, tak ada yang menulis ke sini -> nol perubahan angka.
-- Law 18 (akun baru), 27 (peran runtime), 6 (source_type FK-enforced), 2/26 (pembalik), 24 (tenant_id eksplisit di dokumen).
--   1. CHECK account_roles.role_key += 2 peran (dibangun dari definisi SEKARANG; nilai lama tak terbuang).
--   2. Akun 2-10150 (atribut disalin dari 2-10200 LIABILITY) + 5-10400 (atribut disalin dari 5-10200) per tenant, penjaga tabrakan kode;
--      peran per tenant; seed_default_coa / seed_default_account_roles ditambal untuk tenant BARU (anchor gagal-keras).
--   3. journal_source_types: GOODS_RECEIPT, GOODS_RECEIPT_REVERSAL (void), GOODS_RECEIPT_RETURN (retur sebelum ditagih).
--   4. is_effective_journal: daftar hitam pembalik += GOODS_RECEIPT_REVERSAL.
--   5. Tabel goods_receipts, goods_receipt_items, goods_receipt_bill_links (tagih sebagian FIFO per GRN, §7c).

-- 1. CHECK role_key
DO $$
DECLARE v_def text; v_baru text;
BEGIN
    SELECT pg_get_constraintdef(oid) INTO v_def FROM pg_constraint WHERE conname = 'account_roles_role_key_check';
    IF v_def IS NULL THEN RAISE EXCEPTION 'V452: account_roles_role_key_check tidak ditemukan'; END IF;
    IF v_def NOT LIKE '%ARRAY[%' THEN RAISE EXCEPTION 'V452: bentuk CHECK tak dikenal: %', left(v_def, 120); END IF;
    v_baru := v_def;
    IF v_baru NOT LIKE '%GOODS_RECEIVED_NOT_INVOICED%' THEN
        v_baru := replace(v_baru, 'ARRAY[', 'ARRAY[''GOODS_RECEIVED_NOT_INVOICED''::text, ');
    END IF;
    IF v_baru NOT LIKE '%PURCHASE_PRICE_VARIANCE%' THEN
        v_baru := replace(v_baru, 'ARRAY[', 'ARRAY[''PURCHASE_PRICE_VARIANCE''::text, ');
    END IF;
    IF v_baru <> v_def THEN
        EXECUTE 'ALTER TABLE account_roles DROP CONSTRAINT account_roles_role_key_check';
        EXECUTE 'ALTER TABLE account_roles ADD CONSTRAINT account_roles_role_key_check ' || v_baru;
    END IF;
    v_def := (SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname = 'account_roles_role_key_check');
    IF v_def NOT LIKE '%GOODS_RECEIVED_NOT_INVOICED%' OR v_def NOT LIKE '%PURCHASE_PRICE_VARIANCE%'
       OR v_def NOT LIKE '%GOODS_DELIVERED_NOT_INVOICED%' OR v_def NOT LIKE '%AP_TRADE%' THEN
        RAISE EXCEPTION 'V452: CHECK role_key tidak memuat nilai lama + baru';
    END IF;
END $$;

-- 2a. penjaga tabrakan kode
DO $$
DECLARE n int;
BEGIN
    SELECT count(*) INTO n FROM chart_of_accounts
    WHERE (account_code = '2-10150' AND name <> 'Barang Diterima Belum Ditagih') OR (account_code = '5-10400' AND name <> 'Selisih Harga Pembelian');
    IF n > 0 THEN RAISE EXCEPTION 'V452: kode 2-10150/5-10400 dipakai akun lain (% baris)', n; END IF;
END $$;

-- 2b. akun per tenant (atribut disalin dari pembanding sejenis)
INSERT INTO chart_of_accounts (tenant_id, account_code, name, account_type, normal_balance, parent_code, level, is_header,
                               is_detail, is_system, is_cash, is_bank_account, cash_flow_category, psak_sub_category, category, is_active)
SELECT c.tenant_id, '2-10150', 'Barang Diterima Belum Ditagih', 'LIABILITY', 'CREDIT', c.parent_code, c.level, false,
       c.is_detail, false, false, false, c.cash_flow_category, c.psak_sub_category, c.category, true
FROM chart_of_accounts c
WHERE c.account_code = '2-10200'
  AND NOT EXISTS (SELECT 1 FROM chart_of_accounts x WHERE x.tenant_id = c.tenant_id AND x.account_code = '2-10150');

INSERT INTO chart_of_accounts (tenant_id, account_code, name, account_type, normal_balance, parent_code, level, is_header,
                               is_detail, is_system, is_cash, is_bank_account, cash_flow_category, psak_sub_category, category, is_active)
SELECT c.tenant_id, '5-10400', 'Selisih Harga Pembelian', c.account_type, 'DEBIT', c.parent_code, c.level, false,
       c.is_detail, false, false, false, c.cash_flow_category, c.psak_sub_category, c.category, true
FROM chart_of_accounts c
WHERE c.account_code = '5-10200'
  AND NOT EXISTS (SELECT 1 FROM chart_of_accounts x WHERE x.tenant_id = c.tenant_id AND x.account_code = '5-10400');

INSERT INTO account_roles (tenant_id, role_key, account_id, is_interim, notes)
SELECT a.tenant_id, 'GOODS_RECEIVED_NOT_INVOICED', a.id, false, 'V452 T3: Barang Diterima Belum Ditagih (GRNI, terima sebelum tagihan).'
FROM chart_of_accounts a WHERE a.account_code = '2-10150'
ON CONFLICT (tenant_id, role_key) DO NOTHING;

INSERT INTO account_roles (tenant_id, role_key, account_id, is_interim, notes)
SELECT a.tenant_id, 'PURCHASE_PRICE_VARIANCE', a.id, false, 'V452 T3: Selisih Harga Pembelian (tagihan vs harga terima).'
FROM chart_of_accounts a WHERE a.account_code = '5-10400'
ON CONFLICT (tenant_id, role_key) DO NOTHING;

-- 2c. seed tenant BARU (anchor; gagal-keras bila anchor hilang)
DO $$
DECLARE v_def text; v_baru text;
BEGIN
    v_def := pg_get_functiondef('seed_default_coa(text)'::regprocedure);
    IF v_def NOT LIKE '%2-10150%' THEN
        v_baru := replace(v_def, E'(p_tenant_id, ''2-10100'', ''Hutang Usaha'', ''PAYABLE'', ''CREDIT'', ''2-10000'', 2, false),',
                          E'(p_tenant_id, ''2-10100'', ''Hutang Usaha'', ''PAYABLE'', ''CREDIT'', ''2-10000'', 2, false),\n        (p_tenant_id, ''2-10150'', ''Barang Diterima Belum Ditagih'', ''LIABILITY'', ''CREDIT'', ''2-10000'', 2, false),');
        IF v_baru = v_def THEN RAISE EXCEPTION 'V452: anchor 2-10100 tak ditemukan di seed_default_coa'; END IF;
        IF (length(v_baru) - length(replace(v_baru, 'v_count := v_count + 16;', ''))) / length('v_count := v_count + 16;') <> 1 THEN
            RAISE EXCEPTION 'V452: anchor hitungan blok liabilitas (+16) tidak tunggal';
        END IF;
        v_baru := replace(v_baru, 'v_count := v_count + 16;', 'v_count := v_count + 17;');
        v_def := v_baru;
    END IF;
    IF v_def NOT LIKE '%5-10400%' THEN
        v_baru := replace(v_def, E'(p_tenant_id, ''5-10300'', ''Retur Pembelian'', ''COGS'', ''CREDIT'', ''5-10000'', 2, false, false),',
                          E'(p_tenant_id, ''5-10300'', ''Retur Pembelian'', ''COGS'', ''CREDIT'', ''5-10000'', 2, false, false),\n        (p_tenant_id, ''5-10400'', ''Selisih Harga Pembelian'', ''COGS'', ''DEBIT'', ''5-10000'', 2, false, false),');
        IF v_baru = v_def THEN RAISE EXCEPTION 'V452: anchor 5-10300 tak ditemukan di seed_default_coa'; END IF;
        IF (length(v_baru) - length(replace(v_baru, 'v_count := v_count + 25;', ''))) / length('v_count := v_count + 25;') <> 1 THEN
            RAISE EXCEPTION 'V452: anchor hitungan blok beban (+25) tidak tunggal';
        END IF;
        v_baru := replace(v_baru, 'v_count := v_count + 25;', 'v_count := v_count + 26;');
        v_def := v_baru;
    END IF;
    EXECUTE v_def;
    v_def := pg_get_functiondef('seed_default_account_roles(text)'::regprocedure);
    IF v_def NOT LIKE '%GOODS_RECEIVED_NOT_INVOICED%' THEN
        v_baru := replace(v_def, E'(''AP_TRADE'',                   ''2-10100'', false, NULL),',
                          E'(''AP_TRADE'',                   ''2-10100'', false, NULL),\n            (''GOODS_RECEIVED_NOT_INVOICED'', ''2-10150'', false, ''V452 Barang Diterima Belum Ditagih.''),\n            (''PURCHASE_PRICE_VARIANCE'',    ''5-10400'', false, ''V452 Selisih Harga Pembelian.''),');
        IF v_baru = v_def THEN RAISE EXCEPTION 'V452: anchor AP_TRADE tak ditemukan di seed_default_account_roles'; END IF;
        EXECUTE v_baru;
    END IF;
END $$;

-- 3. source_type jurnal (FK-enforced)
INSERT INTO journal_source_types (source_type, description, is_active) VALUES
    ('GOODS_RECEIPT', 'Penerimaan barang dari PO, barang ber-stok masuk (Dr Persediaan / Cr Barang Diterima Belum Ditagih)', true),
    ('GOODS_RECEIPT_REVERSAL', 'Pembalik penerimaan barang (void GRN sebelum ditagih)', true),
    ('GOODS_RECEIPT_RETURN', 'Retur penerimaan sebelum ditagih (Dr Barang Diterima Belum Ditagih / Cr Persediaan)', true)
ON CONFLICT (source_type) DO NOTHING;

-- 4. is_effective_journal: daftar hitam pembalik
DO $$
DECLARE v_def text; v_baru text;
BEGIN
    v_def := pg_get_functiondef('is_effective_journal(uuid)'::regprocedure);
    IF v_def NOT LIKE '%GOODS_RECEIPT_REVERSAL%' THEN
        v_baru := replace(v_def, '''DELIVERY_NOTE_REVERSAL''', '''DELIVERY_NOTE_REVERSAL'', ''GOODS_RECEIPT_REVERSAL''');
        IF v_baru = v_def THEN RAISE EXCEPTION 'V452: anchor DELIVERY_NOTE_REVERSAL tak ditemukan di is_effective_journal'; END IF;
        EXECUTE v_baru;
    END IF;
END $$;

-- 5. dokumen GRN
CREATE TABLE goods_receipts (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id text NOT NULL,
    receipt_number varchar(30) NOT NULL,
    purchase_order_id uuid NOT NULL REFERENCES purchase_orders(id),
    vendor_id uuid REFERENCES vendors(id),
    receipt_date date NOT NULL,
    warehouse_id uuid REFERENCES warehouses(id),
    status varchar(20) NOT NULL DEFAULT 'posted' CHECK (status IN ('posted', 'void')),
    journal_id uuid REFERENCES journal_entries(id),
    reversal_journal_id uuid REFERENCES journal_entries(id),
    notes text,
    created_at timestamptz NOT NULL DEFAULT now(),
    created_by uuid,
    voided_at timestamptz,
    voided_by uuid,
    voided_reason text,
    CONSTRAINT uq_goods_receipts_number UNIQUE (tenant_id, receipt_number),
    CONSTRAINT chk_gr_void_lengkap CHECK ((status = 'posted' AND voided_at IS NULL) OR (status = 'void' AND voided_at IS NOT NULL))
);
CREATE INDEX idx_goods_receipts_po ON goods_receipts (tenant_id, purchase_order_id);

CREATE TABLE goods_receipt_items (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    goods_receipt_id uuid NOT NULL REFERENCES goods_receipts(id) ON DELETE RESTRICT,
    purchase_order_item_id uuid NOT NULL REFERENCES purchase_order_items(id),
    item_id uuid,
    quantity numeric NOT NULL CHECK (quantity > 0),
    quantity_returned numeric NOT NULL DEFAULT 0 CHECK (quantity_returned >= 0),
    unit_cost numeric NOT NULL CHECK (unit_cost >= 0),
    amount numeric(18,2) NOT NULL,
    ber_stok boolean NOT NULL,
    inventory_ledger_id uuid,
    line_number integer NOT NULL,
    CONSTRAINT chk_gri_retur_maks CHECK (quantity_returned <= quantity)
);
CREATE INDEX idx_gri_receipt ON goods_receipt_items (goods_receipt_id);
CREATE INDEX idx_gri_po_item ON goods_receipt_items (purchase_order_item_id);

CREATE TABLE goods_receipt_bill_links (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    goods_receipt_item_id uuid NOT NULL REFERENCES goods_receipt_items(id),
    bill_item_id uuid NOT NULL REFERENCES bill_items(id),
    quantity numeric NOT NULL CHECK (quantity > 0),
    grni_amount numeric(18,2) NOT NULL,
    variance_amount numeric(18,2) NOT NULL DEFAULT 0,
    status varchar(20) NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'reversed')),
    created_at timestamptz NOT NULL DEFAULT now(),
    reversed_at timestamptz
);
CREATE INDEX idx_grbl_gri ON goods_receipt_bill_links (goods_receipt_item_id) WHERE status = 'active';
CREATE INDEX idx_grbl_bill_item ON goods_receipt_bill_links (bill_item_id);

-- verifikasi akhir
DO $$
DECLARE n_tenant int; n_a1 int; n_a2 int; n_p1 int; n_p2 int;
BEGIN
    SELECT count(DISTINCT tenant_id) INTO n_tenant FROM chart_of_accounts WHERE account_code = '2-10200';
    SELECT count(*) INTO n_a1 FROM chart_of_accounts WHERE account_code = '2-10150' AND name = 'Barang Diterima Belum Ditagih' AND account_type = 'LIABILITY' AND NOT is_header;
    SELECT count(*) INTO n_a2 FROM chart_of_accounts WHERE account_code = '5-10400' AND name = 'Selisih Harga Pembelian' AND NOT is_header;
    SELECT count(*) INTO n_p1 FROM account_roles WHERE role_key = 'GOODS_RECEIVED_NOT_INVOICED';
    SELECT count(*) INTO n_p2 FROM account_roles WHERE role_key = 'PURCHASE_PRICE_VARIANCE';
    IF n_a1 <> n_tenant OR n_p1 <> n_tenant OR n_a2 <> (SELECT count(DISTINCT tenant_id) FROM chart_of_accounts WHERE account_code = '5-10200') OR n_p2 <> n_a2 THEN
        RAISE EXCEPTION 'V452: akun/peran tak lengkap (tenant %, 2-10150 %, peran %, 5-10400 %, peran %)', n_tenant, n_a1, n_p1, n_a2, n_p2;
    END IF;
    IF (SELECT count(*) FROM journal_source_types WHERE source_type IN ('GOODS_RECEIPT', 'GOODS_RECEIPT_REVERSAL', 'GOODS_RECEIPT_RETURN')) <> 3 THEN
        RAISE EXCEPTION 'V452: source_type belum 3';
    END IF;
END $$;
