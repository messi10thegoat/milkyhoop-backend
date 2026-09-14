-- V252 (14 Sep 2026) — FK komposit (customer_id, tenant_id) -> customers(id, tenant_id) untuk dokumen pihak-pelanggan
-- yang belum punya (menegakkan SATU-TENANT di DB, menutup celah lintas-tenant yang ditemukan unit gabung pelanggan).
-- Semua kolom SUDAH uuid (tak ada ubah tipe -> tak perlu restart). Target UNIQUE uq_customers_id_tenant (V247) ada.
-- Lingkup: 4 bertautan simpel (naik ke komposit) + 4 tanpa-FK (tambah). DIKECUALIKAN & dicatat: customer_activities /
-- customer_price_lists (FK CASCADE metadata, semantik beda), dan cheques/item_serials/production_orders/
-- table_reservations (semantik customer_id belum dipastikan pihak-pelanggan; 0 pelanggaran, tunggu konfirmasi).

DO $$
DECLARE
    v_t text;
    v_n bigint;
BEGIN
    FOREACH v_t IN ARRAY ARRAY['sales_invoices','receive_payments','recurring_invoices','sales_receipts',
                               'accounts_receivable','proformas','quotes','sales_orders'] LOOP
        EXECUTE format($f$SELECT count(*) FROM %I x WHERE x.customer_id IS NOT NULL
            AND NOT EXISTS (SELECT 1 FROM customers c WHERE c.id = x.customer_id AND c.tenant_id = x.tenant_id)$f$, v_t)
        INTO v_n;
        IF v_n <> 0 THEN
            RAISE EXCEPTION 'V252: % punya % baris pelanggan yatim/lintas-tenant (diharapkan 0) — BERHENTI, jangan sentuh data', v_t, v_n;
        END IF;
    END LOOP;
END $$;

-- 4 bertautan simpel -> komposit
ALTER TABLE sales_invoices   DROP CONSTRAINT sales_invoices_customer_id_fkey;
ALTER TABLE sales_invoices   ADD CONSTRAINT fk_sales_invoices_customer_tenant   FOREIGN KEY (customer_id, tenant_id) REFERENCES customers (id, tenant_id) ON DELETE NO ACTION;
ALTER TABLE receive_payments DROP CONSTRAINT receive_payments_customer_id_fkey;
ALTER TABLE receive_payments ADD CONSTRAINT fk_receive_payments_customer_tenant FOREIGN KEY (customer_id, tenant_id) REFERENCES customers (id, tenant_id) ON DELETE NO ACTION;
ALTER TABLE recurring_invoices DROP CONSTRAINT recurring_invoices_customer_id_fkey;
ALTER TABLE recurring_invoices ADD CONSTRAINT fk_recurring_invoices_customer_tenant FOREIGN KEY (customer_id, tenant_id) REFERENCES customers (id, tenant_id) ON DELETE NO ACTION;
ALTER TABLE sales_receipts   DROP CONSTRAINT sales_receipts_customer_id_fkey;
ALTER TABLE sales_receipts   ADD CONSTRAINT fk_sales_receipts_customer_tenant   FOREIGN KEY (customer_id, tenant_id) REFERENCES customers (id, tenant_id) ON DELETE NO ACTION;

-- 4 tanpa-FK -> tambah komposit
ALTER TABLE accounts_receivable ADD CONSTRAINT fk_accounts_receivable_customer_tenant FOREIGN KEY (customer_id, tenant_id) REFERENCES customers (id, tenant_id) ON DELETE NO ACTION;
ALTER TABLE proformas    ADD CONSTRAINT fk_proformas_customer_tenant    FOREIGN KEY (customer_id, tenant_id) REFERENCES customers (id, tenant_id) ON DELETE NO ACTION;
ALTER TABLE quotes       ADD CONSTRAINT fk_quotes_customer_tenant       FOREIGN KEY (customer_id, tenant_id) REFERENCES customers (id, tenant_id) ON DELETE NO ACTION;
ALTER TABLE sales_orders ADD CONSTRAINT fk_sales_orders_customer_tenant FOREIGN KEY (customer_id, tenant_id) REFERENCES customers (id, tenant_id) ON DELETE NO ACTION;
