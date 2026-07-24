-- FIX_P1_DEPOSIT 2026-06-16
-- P1 deposit module: support un-apply (application reversal) without violating
-- Law 2/26 (no delete of posted application — reverse, don't delete).
--
-- 1. Add reversal-tracking columns to customer_deposit_applications:
--    - status      : 'active' (default) | 'reversed'  (Law 26 status marker)
--    - reversed_by_id : journal_entries.id of the reversal journal (single pointer)
--    - reversed_at : timestamp
-- 2. Update update_customer_deposit_status() trigger so the cache columns
--    (customer_deposits.amount_applied) EXCLUDE reversed applications. The
--    authoritative balance is journal-derived (Law 16) but cache is kept
--    consistent for backward-compat readers.
--
-- Idempotent: ADD COLUMN IF NOT EXISTS + CREATE OR REPLACE FUNCTION.

ALTER TABLE customer_deposit_applications
    ADD COLUMN IF NOT EXISTS status VARCHAR(20) NOT NULL DEFAULT 'active';

ALTER TABLE customer_deposit_applications
    ADD COLUMN IF NOT EXISTS reversed_by_id UUID;

ALTER TABLE customer_deposit_applications
    ADD COLUMN IF NOT EXISTS reversed_at TIMESTAMPTZ;

-- Trigger: sum only non-reversed applications into the deposit cache.
CREATE OR REPLACE FUNCTION update_customer_deposit_status()
RETURNS trigger
LANGUAGE plpgsql
AS $function$
DECLARE
    v_total_applied BIGINT;
    v_total_refunded BIGINT;
    v_total_amount BIGINT;
    v_new_status VARCHAR(20);
    v_deposit_id UUID;
BEGIN
    IF TG_OP = 'DELETE' THEN
        v_deposit_id := OLD.deposit_id;
    ELSE
        v_deposit_id := NEW.deposit_id;
    END IF;

    SELECT amount, status INTO v_total_amount, v_new_status
    FROM customer_deposits WHERE id = v_deposit_id;

    IF v_new_status IN ('draft', 'void') THEN
        RETURN COALESCE(NEW, OLD);
    END IF;

    -- FIX_P1_DEPOSIT 2026-06-16: exclude reversed applications from cache.
    SELECT COALESCE(SUM(amount_applied), 0) INTO v_total_applied
    FROM customer_deposit_applications
    WHERE deposit_id = v_deposit_id
      AND COALESCE(status, 'active') <> 'reversed';

    SELECT COALESCE(SUM(amount), 0) INTO v_total_refunded
    FROM customer_deposit_refunds WHERE deposit_id = v_deposit_id;

    IF (v_total_applied + v_total_refunded) >= v_total_amount THEN
        v_new_status := 'applied';
    ELSIF (v_total_applied + v_total_refunded) > 0 THEN
        v_new_status := 'partial';
    ELSE
        v_new_status := 'posted';
    END IF;

    UPDATE customer_deposits
    SET amount_applied = v_total_applied,
        amount_refunded = v_total_refunded,
        status = v_new_status,
        updated_at = NOW()
    WHERE id = v_deposit_id;

    RETURN COALESCE(NEW, OLD);
END;
$function$;

COMMENT ON FUNCTION update_customer_deposit_status() IS
'FIX_P1_DEPOSIT 2026-06-16: deposit cache (amount_applied/status) now excludes reversed customer_deposit_applications (status=reversed). Authoritative balance is journal-derived (Law 16); cache kept consistent for backward-compat.';

-- FIX_P1_DEPOSIT 2026-06-16 OPTION B (SP2 decision): the AR/AP obligation
-- guard is NOT relaxed. The earlier (Option A) attempt added
-- DEPOSIT_APPLICATION/DEPOSIT_REFUND to the whitelist so the un-apply
-- reversal (which DEBITS RECEIVABLE) could bypass the obligation check.
-- That weakened the AR guard for an entire source_type and was rejected.
--
-- Option B instead makes the un-apply journal carry the REAL invoice
-- obligation in journal_entries.source_id (the invoice that was settled),
-- exactly the way an invoice posting satisfies the guard. The guard's
-- RECEIVABLE-debit branch then finds EXISTS(sales_invoices WHERE id =
-- source_id) and passes NATURALLY -- no whitelist, no weakening.
--
-- This statement RESTORES the original (un-relaxed) guard definition,
-- byte-identical to the pre-V177 production function (which already
-- contained the broader BILL_PAYMENT/CREDIT_NOTE_REFUND/JOURNAL_ENTRY
-- whitelist applied to the live DB before P1). It is idempotent and
-- corrects any environment that ran the Option-A relaxation.
CREATE OR REPLACE FUNCTION public.guard_arap_requires_obligation()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
DECLARE
    v_account_type TEXT;
    v_source_type TEXT;
    v_source_id TEXT;
    v_obligation_exists BOOLEAN;
    v_whitelisted TEXT[] := ARRAY[
        'BILL', 'PAYMENT_BILL', 'PAYMENT_MADE', 'BILL_PAYMENT',
        'INVOICE', 'SALES_INVOICE_COGS', 'CASH_SALE',
        'PAYMENT_RECEIVED', 'RECEIVE_PAYMENT', 'INVOICE_REVERSAL',
        'OPENING', 'OPENING_BALANCE', 'MANUAL', 'ADJUSTMENT',
        'REVERSAL', 'CLOSING', 'REVALUATION',
        'CREDIT_NOTE', 'CREDIT_NOTE_REFUND',
        'VENDOR_CREDIT', 'VENDOR_CREDIT_REFUND',
        'VENDOR_DEPOSIT',
        'CUSTOMER_DEPOSIT', 'BANK_TRANSACTION', 'BANK_TRANSFER',
        'RECONCILIATION_ADJUSTMENT', 'STOCK_ADJUSTMENT',
        'EXPENSE', 'SALES_RECEIPT', 'PAYROLL',
        'FIXED_ASSET', 'DEPRECIATION', 'ASSET_DISPOSAL', 'ASSET_SALE',
        'JOURNAL_ENTRY'
    ];
BEGIN
    SELECT coa.account_type INTO v_account_type
    FROM chart_of_accounts coa WHERE coa.id = NEW.account_id;
    IF v_account_type NOT IN ('RECEIVABLE', 'PAYABLE') THEN RETURN NEW; END IF;
    SELECT je.source_type, je.source_id::text INTO v_source_type, v_source_id
    FROM journal_entries je WHERE je.id = NEW.journal_id;
    IF v_source_type = ANY(v_whitelisted) THEN RETURN NEW; END IF;
    IF v_account_type = 'PAYABLE' AND NEW.credit > 0 THEN
        BEGIN
            SELECT EXISTS(SELECT 1 FROM bills WHERE id = v_source_id::uuid) INTO v_obligation_exists;
        EXCEPTION WHEN invalid_text_representation THEN
            RAISE EXCEPTION 'Law 29/30: Non-UUID source_id cannot touch PAYABLE. source_type=%, source_id=%', v_source_type, v_source_id;
        END;
        IF NOT v_obligation_exists THEN
            RAISE EXCEPTION 'Law 29/30: Cannot credit PAYABLE without bill. source_type=%, source_id=%', v_source_type, v_source_id;
        END IF;
    END IF;
    IF v_account_type = 'RECEIVABLE' AND NEW.debit > 0 THEN
        BEGIN
            SELECT EXISTS(SELECT 1 FROM sales_invoices WHERE id = v_source_id::uuid) INTO v_obligation_exists;
        EXCEPTION WHEN invalid_text_representation THEN
            RAISE EXCEPTION 'Law 29/30: Non-UUID source_id cannot touch RECEIVABLE. source_type=%, source_id=%', v_source_type, v_source_id;
        END;
        IF NOT v_obligation_exists THEN
            RAISE EXCEPTION 'Law 29/30: Cannot debit RECEIVABLE without invoice. source_type=%, source_id=%', v_source_type, v_source_id;
        END IF;
    END IF;
    RETURN NEW;
END;
$function$;

-- FIX_P1_DEPOSIT 2026-06-16 (a): allow re-apply after un-apply. The blanket
-- UNIQUE(deposit_id, invoice_id) blocked a NEW active application to the same
-- invoice once a prior application was reversed (the reversed row still
-- occupies the key). Replace with a PARTIAL unique index that ignores
-- reversed rows, preserving the "one ACTIVE application per (deposit,invoice)"
-- invariant while permitting re-apply.
ALTER TABLE customer_deposit_applications
    DROP CONSTRAINT IF EXISTS uq_cust_deposit_application;

DROP INDEX IF EXISTS uq_cust_deposit_application_active;
CREATE UNIQUE INDEX uq_cust_deposit_application_active
    ON customer_deposit_applications (deposit_id, invoice_id)
    WHERE COALESCE(status, 'active') <> 'reversed';
