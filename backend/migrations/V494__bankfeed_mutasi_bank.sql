-- V494: BANK FEED gelombang 1 (10 Nov 2026) -- mutasi rekening koran yang diimpor (KlikBCA CSV/PDF), keputusan per baris (cocok/catat/
-- pribadi/kecualikan/pisah), alokasi cocok n:m append-only, aturan bank, + kolom uang bank_reconciliations ke numeric(18,2) dan kolom
-- batal rekonsiliasi (histori permanen). Kontrak: ~/milkyhoop-docs-rute/KONTRAK-bankfeed-rekonsiliasi-20261110.md §2.
-- DISETUJUI LANGSUNG pemilik di sesi BACKEND 10 Nov 2026 (AskUserQuestion, DDL final). NOL baris lama berubah: tabel baru kosong;
-- bank_reconciliations 0 baris di semua tenant (terukur 10 Nov); chk_da_entity hanya MENAMBAH 'mutasi_bank_impor'.
-- Nol jurnal. Dijalankan apply_mig.sh (satu transaksi, -1). Pembalik: V494__bankfeed_mutasi_bank_ROLLBACK.sql (gagal keras bila sudah dipakai).

CREATE TABLE mutasi_bank_impor (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id text NOT NULL,
  bank_account_id uuid NOT NULL REFERENCES bank_accounts(id),
  document_id uuid NOT NULL REFERENCES documents(id),
  format text NOT NULL CHECK (format IN ('klikbca_csv','klikbca_pdf','csv','xlsx','ofx')),
  peta_kolom jsonb,
  tanggal_awal date, tanggal_akhir date,
  saldo_awal_berkas numeric(18,2), saldo_akhir_berkas numeric(18,2),
  jumlah_baru int NOT NULL, jumlah_duplikat int NOT NULL, jumlah_dilewati int NOT NULL,
  dibuat_oleh uuid, dibuat_pada timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_mutasi_bank_impor_rekening ON mutasi_bank_impor (tenant_id, bank_account_id, dibuat_pada DESC);

CREATE TABLE mutasi_bank (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id text NOT NULL,
  bank_account_id uuid NOT NULL REFERENCES bank_accounts(id),
  impor_id uuid REFERENCES mutasi_bank_impor(id),
  induk_id uuid REFERENCES mutasi_bank(id),
  tanggal date NOT NULL,
  keterangan text NOT NULL, keterangan_normal text NOT NULL,
  arah text NOT NULL CHECK (arah IN ('masuk','keluar')),
  nominal numeric(18,2) NOT NULL CHECK (nominal > 0),
  saldo_berkas numeric(18,2),
  sidik text NOT NULL,
  baris_ke int, baris_mentah text,
  duplikat_dari uuid REFERENCES mutasi_bank(id),
  status text NOT NULL DEFAULT 'untuk_ditinjau'
    CHECK (status IN ('untuk_ditinjau','mungkin_duplikat','dicatat','dikecualikan','dipisah')),
  cara text CHECK (cara IN ('cocok','catat','pribadi','kecualikan')),
  alasan text,
  akun_id uuid, kontak_jenis text, kontak_id uuid,
  aturan_id uuid, diputus_oleh uuid, diputus_pada timestamptz,
  rekonsiliasi_id uuid REFERENCES bank_reconciliations(id),
  dibuat_pada timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, bank_account_id, sidik)
);
CREATE INDEX ix_mutasi_bank_daftar ON mutasi_bank (tenant_id, bank_account_id, status, tanggal);

CREATE TABLE mutasi_bank_tautan (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id text NOT NULL,
  mutasi_bank_id uuid NOT NULL REFERENCES mutasi_bank(id),
  bank_transaction_id uuid NOT NULL REFERENCES bank_transactions(id),
  nominal_alokasi numeric(18,2) NOT NULL CHECK (nominal_alokasi > 0),
  dibuat_oleh uuid, dibuat_pada timestamptz NOT NULL DEFAULT now(),
  dilepas_oleh uuid, dilepas_pada timestamptz
);
CREATE UNIQUE INDEX ux_mutasi_bank_tautan_aktif ON mutasi_bank_tautan (mutasi_bank_id, bank_transaction_id) WHERE dilepas_pada IS NULL;
CREATE INDEX ix_mutasi_bank_tautan_bt ON mutasi_bank_tautan (tenant_id, bank_transaction_id) WHERE dilepas_pada IS NULL;

CREATE TABLE aturan_bank (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id text NOT NULL,
  bank_account_id uuid REFERENCES bank_accounts(id),
  nama text NOT NULL,
  arah text CHECK (arah IN ('masuk','keluar')),
  syarat jsonb NOT NULL,
  logika text NOT NULL DEFAULT 'semua' CHECK (logika IN ('semua','salah_satu')),
  tindakan jsonb NOT NULL,
  prioritas int NOT NULL DEFAULT 100,
  aktif bool NOT NULL DEFAULT true,
  dibuat_oleh uuid, dibuat_pada timestamptz NOT NULL DEFAULT now(), diubah_pada timestamptz
);
CREATE INDEX ix_aturan_bank_tenant ON aturan_bank (tenant_id, aktif, prioritas);

-- RLS + FORCE (pola Law 24 = rls_bank_transactions; pagar nyata tetap tenant eksplisit di setiap SQL gateway)
ALTER TABLE mutasi_bank_impor ENABLE ROW LEVEL SECURITY;  ALTER TABLE mutasi_bank_impor FORCE ROW LEVEL SECURITY;
ALTER TABLE mutasi_bank ENABLE ROW LEVEL SECURITY;        ALTER TABLE mutasi_bank FORCE ROW LEVEL SECURITY;
ALTER TABLE mutasi_bank_tautan ENABLE ROW LEVEL SECURITY; ALTER TABLE mutasi_bank_tautan FORCE ROW LEVEL SECURITY;
ALTER TABLE aturan_bank ENABLE ROW LEVEL SECURITY;        ALTER TABLE aturan_bank FORCE ROW LEVEL SECURITY;
CREATE POLICY rls_mutasi_bank_impor ON mutasi_bank_impor USING (tenant_id = current_setting('app.tenant_id', true));
CREATE POLICY rls_mutasi_bank ON mutasi_bank USING (tenant_id = current_setting('app.tenant_id', true));
CREATE POLICY rls_mutasi_bank_tautan ON mutasi_bank_tautan USING (tenant_id = current_setting('app.tenant_id', true));
CREATE POLICY rls_aturan_bank ON aturan_bank USING (tenant_id = current_setting('app.tenant_id', true));

-- bank_reconciliations: 0 baris (terukur) -> ubah tipe aman; batal = status + histori
DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM bank_reconciliations) THEN
    RAISE EXCEPTION 'V494: bank_reconciliations tidak kosong -- hentikan, ukur ulang sebelum mengubah tipe kolom uang';
  END IF;
END $$;
ALTER TABLE bank_reconciliations
  ALTER COLUMN difference TYPE numeric(18,2),
  ALTER COLUMN reconciled_deposits TYPE numeric(18,2),
  ALTER COLUMN reconciled_withdrawals TYPE numeric(18,2),
  ALTER COLUMN unreconciled_deposits TYPE numeric(18,2),
  ALTER COLUMN unreconciled_withdrawals TYPE numeric(18,2),
  ADD COLUMN dibatalkan_oleh uuid,
  ADD COLUMN dibatalkan_pada timestamptz,
  ADD COLUMN alasan_batal text;

-- lampiran berkas impor (pola V493: hanya menambah satu nilai)
ALTER TABLE document_attachments DROP CONSTRAINT IF EXISTS chk_da_entity;
ALTER TABLE document_attachments ADD CONSTRAINT chk_da_entity CHECK (entity_type IN (
  'sales_invoice','bill','expense','customer','vendor','item','journal','quote',
  'purchase_order','sales_order','sales_receipt','payment','credit_note','vendor_credit',
  'stock_adjustment','stock_transfer','employee','asset','project','contract',
  'chat_message','other','customer_deposit','proforma','delivery',
  'goods_receipt','vendor_deposit',
  'bank_transaction','bank_transfer','employee_advance',
  'mutasi_bank_impor'
));

