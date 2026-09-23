-- V293 (23 Sep 2026): tax_invoices + tax_invoice_sources -- class-A reconstruction, CODE AS ARBITER.
--
-- Why now (RECOVERY_MISSING_TABLES_BACKLOG.md policy, owner directive 24 Jul: create only on an
-- OBSERVED failure): GET /api/tax-invoices/by-source runs for every PKP tenant on invoice detail
-- and 500s with UndefinedTableError "tax_invoice_sources" (observed live 23 Sep; grapgrap is PKP).
-- No DDL for these tables exists in any branch, DOCS or recovery note (git log -S, all refs).
--
-- SCOPE = option (2): ONLY these two tables. The rest of the e-Faktur module (tax_info,
-- tax_invoice_items, product_djp_mapping, nsfp_assignments, efaktur_exports, djp_* catalogs) is
-- deliberately NOT created; write routes answer 409 "Fitur Faktur Pajak belum diaktifkan" through
-- services/efaktur_module.require_efaktur_module until that module exists. The DJP catalogs have
-- no source in the repo and are not invented.
--
-- Every column below is taken from routers/tax_invoices.py (ti) / routers/efaktur.py (ef) usage,
-- so that the later full-module unit does not have to reshape these tables:
--   INSERT columns ti:392 / ti:1289; UPDATE ti:542, ti:1086, ti:1154, ti:1210, ti:1251, ti:1393,
--   ef:303; filters/selects ti:737-743 (list), ti:819 (by-source), ef:158/219 (export).
-- masa_pajak / tahun_pajak are READ (list filter ti:717-722, export ef:158) but written nowhere:
--   derived from faktur_date as GENERATED columns ('01'..'12' string per schemas/efaktur.py:17-20,
--   int year per schemas/tax_invoices.py:33). This is a derivation, stated as such.
-- Status vocabulary = VALID_TRANSITIONS (ti:26-32) + 'cancelled' + 'replaced'.
-- direction vocabulary = 'keluaran' | 'masukan' (ti:96,183,274).
-- XML header fields keterangan_tambahan / cap_fasilitas are read via dict.get() and written
-- nowhere -> not created (they export as empty, same as today's code intends).
-- Idempotent (IF NOT EXISTS). Money NUMERIC(18,2); rates NUMERIC(9,4).

CREATE TABLE IF NOT EXISTS tax_invoices (
    id                      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id               text NOT NULL,                       -- ti:392 $1; every filter
    direction               text NOT NULL CHECK (direction IN ('keluaran', 'masukan')),  -- ti:96,183,274
    faktur_date             date NOT NULL,                       -- ti:392 $3
    masa_pajak              text GENERATED ALWAYS AS (lpad(extract(month FROM faktur_date)::int::text, 2, '0')) STORED,
    tahun_pajak             integer GENERATED ALWAYS AS (extract(year FROM faktur_date)::int) STORED,
    kode_transaksi          text,                                -- ti:392 $4
    faktur_number           text,                                -- ti:1086, ti:1154
    nsfp_number             text,                                -- ti:1086, ti:1154
    npwp_penjual            text,
    nitku_penjual           text,
    nama_penjual            text,
    alamat_penjual          text,
    npwp_pembeli            text,
    nik_pembeli             text,
    jenis_id_pembeli        text,
    negara_pembeli          text,
    nomor_dokumen_pembeli   text,
    nama_pembeli            text,
    alamat_pembeli          text,
    email_pembeli           text,
    nitku_pembeli           text,
    referensi               text,
    retur_of_tax_invoice_id uuid REFERENCES tax_invoices(id),    -- ti:392 $19
    retur_of_faktur_number  text,                                -- ti:392 $20
    source_document_type    text,                                -- ti:392 $21, ti:819
    dpp                     numeric(18,2) NOT NULL DEFAULT 0,    -- ti:392 literal 0, ti:542
    dpp_nilai_lain          numeric(18,2) NOT NULL DEFAULT 0,
    ppn                     numeric(18,2) NOT NULL DEFAULT 0,
    ppnbm                   numeric(18,2) NOT NULL DEFAULT 0,
    tarif_ppn               numeric(9,4)  NOT NULL DEFAULT 0,
    grand_total             numeric(18,2) NOT NULL DEFAULT 0,
    status                  text NOT NULL DEFAULT 'draft' CHECK (status IN
                              ('draft', 'nsfp_assigned', 'exported', 'uploaded', 'approved', 'cancelled', 'replaced')),
    fg_pengganti            integer NOT NULL DEFAULT 0,          -- ti:392, ti:1289 literal 1
    replaces_id             uuid REFERENCES tax_invoices(id),    -- ti:392, ti:1289
    replaced_by_id          uuid REFERENCES tax_invoices(id),    -- ti:1393
    notes                   text,                                -- ti:1289
    cancellation_reason     text,                                -- ti:1251
    cancelled_at            timestamptz,
    cancelled_by            uuid,
    created_by              uuid,                                -- ti:392, ti:1289 (user id)
    created_at              timestamptz NOT NULL DEFAULT now(),  -- ti:224 ORDER BY, ti:743
    updated_at              timestamptz NOT NULL DEFAULT now()   -- ti:1086, ti:1210, ef:303
);
CREATE INDEX IF NOT EXISTS ix_tax_invoices_tenant_period ON tax_invoices (tenant_id, direction, tahun_pajak, masa_pajak, status);
CREATE UNIQUE INDEX IF NOT EXISTS uq_tax_invoices_tenant_faktur_number ON tax_invoices (tenant_id, faktur_number) WHERE faktur_number IS NOT NULL;

CREATE TABLE IF NOT EXISTS tax_invoice_sources (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       text NOT NULL,                                -- ti:442 $1
    tax_invoice_id  uuid NOT NULL REFERENCES tax_invoices(id) ON DELETE CASCADE,  -- ti:442 $2
    source_type     text NOT NULL CHECK (source_type IN ('sales_invoice', 'credit_note', 'vendor_credit', 'bill')),  -- ti:122,208,297,313
    source_id       uuid NOT NULL,                                -- ti:442 $4 (compared as ::text at ti:819)
    created_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_tax_invoice_sources_source ON tax_invoice_sources (tenant_id, source_type, source_id);
CREATE INDEX IF NOT EXISTS ix_tax_invoice_sources_ti ON tax_invoice_sources (tax_invoice_id);
