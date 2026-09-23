-- V299: LEPAS PEMBAYARAN (unapply a receive-payment allocation from an invoice). Owner-ordered 23 Sep 2026.
-- Standard pattern (SAP FBRA reset cleared items / NetSuite unapply / Xero remove & redo).
-- Journal = NEW entries only (Law 2): Dr Piutang Usaha / Cr Uang Muka Pelanggan, source_type
-- RECEIVE_PAYMENT_UNAPPLY, source_id = the INVOICE (Law 29/30 obligation reference; the AR guard trigger
-- requires it for a non-whitelisted AR debit). The freed credit becomes a dedicated customer deposit
-- (LPS-...) linked via the allocation row, re-appliable through the EXISTING deposit /apply path.
ALTER TABLE receive_payment_allocations
    ADD COLUMN IF NOT EXISTS status varchar(20) NOT NULL DEFAULT 'active',
    ADD COLUMN IF NOT EXISTS reversed_at timestamptz,
    ADD COLUMN IF NOT EXISTS reversed_by uuid,
    ADD COLUMN IF NOT EXISTS unapply_journal_id uuid REFERENCES journal_entries(id),
    ADD COLUMN IF NOT EXISTS unapply_deposit_id uuid REFERENCES customer_deposits(id),
    ADD COLUMN IF NOT EXISTS unapply_reason text;
ALTER TABLE receive_payment_allocations DROP CONSTRAINT IF EXISTS chk_rpa_status;
ALTER TABLE receive_payment_allocations ADD CONSTRAINT chk_rpa_status CHECK (status IN ('active', 'reversed'));
CREATE UNIQUE INDEX IF NOT EXISTS uq_rpa_unapply_journal ON receive_payment_allocations (unapply_journal_id) WHERE unapply_journal_id IS NOT NULL;

INSERT INTO journal_source_types (source_type, description, is_active)
VALUES ('RECEIVE_PAYMENT_UNAPPLY', 'Lepas pembayaran: alokasi pelunasan dilepas dari faktur; kredit menjadi uang muka pelanggan', true)
ON CONFLICT (source_type) DO NOTHING;

CREATE OR REPLACE FUNCTION public.compute_ar_outstanding(p_tenant_id text)
 RETURNS TABLE(customer_id text, customer_name text, invoice_id uuid, invoice_number text, invoice_date date, due_date date, invoice_status text, invoice_total numeric, paid_amount numeric, outstanding numeric)
 LANGUAGE plpgsql
 STABLE
AS $function$
BEGIN
    RETURN QUERY
    WITH
    active_invoices AS (
        SELECT si.id, si.invoice_number, si.invoice_date, si.due_date,
               si.customer_id, si.customer_name, si.status, si.total_amount
        FROM sales_invoices si
        WHERE si.tenant_id = p_tenant_id
          AND si.status NOT IN ('draft', 'void')
    ),
    invoice_debits AS (
        SELECT je.source_id::uuid AS inv_id,
               COALESCE(SUM(jl.debit), 0) AS total_debit
        FROM journal_lines jl
        JOIN journal_entries je ON je.id = jl.journal_id
        JOIN chart_of_accounts coa ON coa.id = jl.account_id
        WHERE je.tenant_id = p_tenant_id
          AND je.status = 'POSTED' AND je.reversed_by_id IS NULL
          AND coa.account_type = 'RECEIVABLE' AND jl.debit > 0
          AND je.source_type = 'INVOICE'
        GROUP BY je.source_id::uuid
    ),
    payment_credits AS (
        -- Branch 1: receive_payment settlements (via allocations)
        -- V245 (13 Sep 2026): PROPORSIONAL per alokasi (kembar AP; lihat compute_ap_outstanding).
        -- Versi lama memberi SELURUH kredit AR jurnal penerimaan ke SETIAP faktur yang dialokasi;
        -- cache amount_paid (dihitung ulang dari fungsi ini) ikut ganda -> faktur "lunas" palsu.
        SELECT x.invoice_id AS inv_id,
               COALESCE(SUM(x.bagian + CASE WHEN x.urut_akhir = 1 AND x.jumlah_bagian > 0
                                            THEN x.ar_credit - x.jumlah_bagian ELSE 0 END), 0) AS total_credit
        FROM (
            SELECT b.*, SUM(b.bagian) OVER (PARTITION BY b.payment_id) AS jumlah_bagian
            FROM (
                SELECT rpa.invoice_id, rpa.payment_id, pb.ar_credit,
                       CASE WHEN SUM(rpa.amount_applied) OVER (PARTITION BY rpa.payment_id) > 0
                            THEN ROUND(pb.ar_credit * rpa.amount_applied
                                       / SUM(rpa.amount_applied) OVER (PARTITION BY rpa.payment_id), 2)
                            ELSE 0 END AS bagian,
                       ROW_NUMBER() OVER (PARTITION BY rpa.payment_id ORDER BY rpa.id DESC) AS urut_akhir
                FROM receive_payment_allocations rpa
                JOIN (
                    SELECT rp.id AS payment_id, COALESCE(SUM(jl.credit), 0) AS ar_credit
                    FROM receive_payments rp
                    JOIN journal_entries je ON je.id = rp.journal_id
                    JOIN journal_lines jl ON jl.journal_id = je.id
                    JOIN chart_of_accounts coa ON coa.id = jl.account_id
                    WHERE rp.tenant_id = p_tenant_id
                      AND je.status = 'POSTED' AND je.reversed_by_id IS NULL
                      AND coa.account_type = 'RECEIVABLE' AND jl.credit > 0
                    GROUP BY rp.id
                ) pb ON pb.payment_id = rpa.payment_id
            ) b
        ) x
        GROUP BY x.invoice_id

        UNION ALL

        -- Branch 2: credit-note applications (original_invoice_id link)
        SELECT cn.original_invoice_id AS inv_id, COALESCE(SUM(jl.credit), 0) AS total_credit
        FROM credit_notes cn
        JOIN journal_entries je ON je.source_id::uuid = cn.id
            AND je.source_type = 'CREDIT_NOTE'
        JOIN journal_lines jl ON jl.journal_id = je.id
        JOIN chart_of_accounts coa ON coa.id = jl.account_id
        WHERE cn.tenant_id = p_tenant_id
          AND cn.original_invoice_id IS NOT NULL
          AND je.status = 'POSTED' AND je.reversed_by_id IS NULL
          AND coa.account_type = 'RECEIVABLE' AND jl.credit > 0
        GROUP BY cn.original_invoice_id

        UNION ALL

        -- Branch 3 (FIX_P35_ARCANON): customer-deposit applications.
        SELECT cda.invoice_id AS inv_id, COALESCE(SUM(jl.credit), 0) AS total_credit
        FROM customer_deposit_applications cda
        JOIN journal_entries je ON je.id = cda.journal_id
            AND je.source_type = 'DEPOSIT_APPLICATION'
        JOIN journal_lines jl ON jl.journal_id = je.id
        JOIN chart_of_accounts coa ON coa.id = jl.account_id
        WHERE cda.tenant_id = p_tenant_id
          AND cda.status = 'active'
          AND je.status = 'POSTED' AND je.reversed_by_id IS NULL
          AND coa.account_type = 'RECEIVABLE' AND jl.credit > 0
        GROUP BY cda.invoice_id

        UNION ALL

        -- Branch 4 (V299): LEPAS PEMBAYARAN. The unapply journal (source_type RECEIVE_PAYMENT_UNAPPLY,
        -- source_id = the invoice, Law 29/30) debits Piutang; it cancels that allocation's share of the
        -- receive-payment credit (Branch 1 keeps counting it), so it enters as a NEGATIVE credit.
        SELECT je.source_id::uuid AS inv_id, -COALESCE(SUM(jl.debit), 0) AS total_credit
        FROM journal_entries je
        JOIN journal_lines jl ON jl.journal_id = je.id
        JOIN chart_of_accounts coa ON coa.id = jl.account_id
        WHERE je.tenant_id = p_tenant_id
          AND je.source_type = 'RECEIVE_PAYMENT_UNAPPLY'
          AND je.status = 'POSTED' AND je.reversed_by_id IS NULL
          AND coa.account_type = 'RECEIVABLE' AND jl.debit > 0
        GROUP BY je.source_id::uuid
    ),
    aggregated_credits AS (
        SELECT pc.inv_id, SUM(pc.total_credit) AS total_credit
        FROM payment_credits pc
        GROUP BY pc.inv_id
    ),
    -- V270: unapplied credit notes (original_invoice_id IS NULL) — RECEIVABLE credit
    -- booked at CN posting but attributed to NO invoice. Net at customer level.
    -- DISJOINT from Branch 2 (which takes original_invoice_id IS NOT NULL) -> no double count.
    cn_unapplied AS (
        SELECT cn.customer_id,
               cn.customer_name,
               COALESCE(SUM(jl.credit), 0) AS total_credit,
               COALESCE(SUM(jl.debit), 0)  AS total_debit
        FROM journal_entries je
        JOIN journal_lines jl ON jl.journal_id = je.id
        JOIN chart_of_accounts coa ON coa.id = jl.account_id
        JOIN credit_notes cn ON cn.id = je.source_id::uuid
        WHERE je.tenant_id = p_tenant_id
          AND je.status = 'POSTED' AND je.reversed_by_id IS NULL
          AND je.source_type = 'CREDIT_NOTE'
          AND coa.account_type = 'RECEIVABLE'
          AND cn.original_invoice_id IS NULL
        GROUP BY cn.customer_id, cn.customer_name
    ),
    inv_rows AS (
        SELECT ai.customer_id::TEXT AS customer_id, ai.customer_name::TEXT AS customer_name,
               ai.id AS invoice_id, ai.invoice_number::TEXT AS invoice_number,
               ai.invoice_date, ai.due_date, ai.status::TEXT AS invoice_status,
               COALESCE(id2.total_debit, 0)::NUMERIC(18,2) AS invoice_total,
               COALESCE(ac.total_credit, 0)::NUMERIC(18,2) AS paid_amount,
               (COALESCE(id2.total_debit, 0) - COALESCE(ac.total_credit, 0))::NUMERIC(18,2) AS outstanding
        FROM active_invoices ai
        LEFT JOIN invoice_debits id2 ON id2.inv_id = ai.id
        LEFT JOIN aggregated_credits ac ON ac.inv_id = ai.id
        WHERE COALESCE(id2.total_debit, 0) - COALESCE(ac.total_credit, 0) != 0
    ),
    -- Synthetic customer-level row for unapplied CNs (mirror AP vc_rows). RECEIVABLE is
    -- debit-normal, so a CN credit yields NEGATIVE outstanding (reduces AR).
    cn_rows AS (
        SELECT cnu.customer_id::TEXT AS customer_id, cnu.customer_name::TEXT AS customer_name,
               NULL::UUID AS invoice_id, 'CREDIT-NOTE'::TEXT AS invoice_number,
               CURRENT_DATE AS invoice_date, CURRENT_DATE AS due_date,
               'credit_note'::TEXT AS invoice_status,
               cnu.total_credit::NUMERIC(18,2) AS invoice_total,
               cnu.total_debit::NUMERIC(18,2)  AS paid_amount,
               (cnu.total_debit - cnu.total_credit)::NUMERIC(18,2) AS outstanding
        FROM cn_unapplied cnu
    )
    SELECT * FROM (
        SELECT * FROM inv_rows
        UNION ALL
        SELECT * FROM cn_rows WHERE cn_rows.outstanding <> 0
    ) q
    ORDER BY q.customer_name, q.due_date;
END;
$function$;

CREATE OR REPLACE FUNCTION public.compute_ar_adjustments(p_tenant_id text)
 RETURNS TABLE(journal_id uuid, journal_number text, journal_date date, source_type text, description text, debit numeric, credit numeric, net numeric)
 LANGUAGE sql
 STABLE
AS $function$
    SELECT je.id, je.journal_number, je.journal_date::date,
           je.source_type, je.description,
           COALESCE(SUM(jl.debit), 0),
           COALESCE(SUM(jl.credit), 0),
           COALESCE(SUM(jl.debit - jl.credit), 0)
    FROM journal_lines jl
    JOIN journal_entries je ON je.id = jl.journal_id
    JOIN chart_of_accounts coa ON coa.id = jl.account_id
    WHERE coa.account_type = 'RECEIVABLE'
      AND je.status = 'POSTED'
      AND je.reversed_by_id IS NULL
      AND je.tenant_id = p_tenant_id
      AND (
        -- Non-standard source types (orphaned journals)
        -- FIX_P35_ARCANON: DEPOSIT_APPLICATION added to the recognized list so it
        -- is NO LONGER treated as an orphan adjustment. It is now counted as a
        -- settlement in compute_ar_outstanding() (Branch 3). Leaving it here would
        -- double-subtract the applied deposit.
        je.source_type NOT IN ('INVOICE', 'PAYMENT_RECEIVED', 'RECEIVE_PAYMENT', 'SALES_INVOICE_COGS', 'CASH_SALE', 'INVOICE_REVERSAL', 'DEPOSIT_APPLICATION', 'RECEIVE_PAYMENT_UNAPPLY')
        -- Invoices without active obligation (voided/drafted)
        OR (je.source_type = 'INVOICE' AND NOT EXISTS (
            SELECT 1 FROM sales_invoices si
            WHERE si.id = je.source_id::uuid
              AND si.tenant_id = p_tenant_id
              AND si.status NOT IN ('draft', 'void')
        ))
        -- Payments without active obligation link
        OR (je.source_type IN ('PAYMENT_RECEIVED', 'RECEIVE_PAYMENT') AND NOT EXISTS (
            SELECT 1 FROM receive_payment_allocations rpa
            JOIN receive_payments rp ON rp.id = rpa.payment_id
            JOIN sales_invoices si ON si.id = rpa.invoice_id
            WHERE rp.journal_id = je.id
              AND si.tenant_id = p_tenant_id
              AND si.status NOT IN ('draft', 'void')
        ))
        -- V299: unapply journals whose invoice is no longer active (voided after the unapply) -- the
        -- mirror of the RECEIVE_PAYMENT clause above, so both legs leave the invoice together
        OR (je.source_type = 'RECEIVE_PAYMENT_UNAPPLY' AND NOT EXISTS (
            SELECT 1 FROM sales_invoices si
            WHERE si.id = je.source_id::uuid
              AND si.tenant_id = p_tenant_id
              AND si.status NOT IN ('draft', 'void')
        ))
        -- Invoice reversals (void credit to RECEIVABLE)
        OR je.source_type = 'INVOICE_REVERSAL'
      )
    GROUP BY je.id, je.journal_number, je.journal_date, je.source_type, je.description;
$function$;

CREATE OR REPLACE FUNCTION public.ar_klaim_piutang(p_tenant_id text)
 RETURNS TABLE(line_id uuid, journal_id uuid, invoice_id uuid, amount numeric, cara text)
 LANGUAGE sql
 STABLE
AS $function$
    WITH baris AS (
        SELECT jl.id AS line_id, je.id AS journal_id, je.source_type, je.source_id, je.reversal_of_id,
               jl.account_id, (jl.debit - jl.credit)::numeric(18,2) AS net
        FROM journal_lines jl
        JOIN journal_entries je ON je.id = jl.journal_id
        JOIN chart_of_accounts coa ON coa.id = jl.account_id
        WHERE je.tenant_id = p_tenant_id AND je.status = 'POSTED' AND coa.account_type = 'RECEIVABLE'
    ),
    asal AS (
        -- faktur
        SELECT b.line_id, b.journal_id, si.id AS invoice_id, b.net AS amount, 'INVOICE'::text AS cara
        FROM baris b JOIN sales_invoices si ON si.id = b.source_id AND si.tenant_id = p_tenant_id
        WHERE b.reversal_of_id IS NULL AND b.source_type = 'INVOICE'
        UNION ALL
        -- nota kredit yang terkait faktur
        SELECT b.line_id, b.journal_id, cn.original_invoice_id, b.net, 'CREDIT_NOTE'
        FROM baris b JOIN credit_notes cn ON cn.id = b.source_id AND cn.tenant_id = p_tenant_id
        WHERE b.reversal_of_id IS NULL AND b.source_type = 'CREDIT_NOTE' AND cn.original_invoice_id IS NOT NULL
        UNION ALL
        -- penerapan uang muka: tepat SATU aplikasi per jurnal, selain itu tak diklaim (residu berisik)
        SELECT b.line_id, b.journal_id, cda.invoice_id, b.net, 'DEPOSIT_APPLICATION'
        FROM baris b
        JOIN customer_deposit_applications cda ON cda.journal_id = b.journal_id AND cda.tenant_id = p_tenant_id
        WHERE b.reversal_of_id IS NULL AND b.source_type = 'DEPOSIT_APPLICATION'
          AND (SELECT count(*) FROM customer_deposit_applications c2 WHERE c2.journal_id = b.journal_id) = 1
        UNION ALL
        -- V299 lepas pembayaran: baris Dr Piutang milik faktur source_id
        SELECT b.line_id, b.journal_id, si.id, b.net, 'RECEIVE_PAYMENT_UNAPPLY'
        FROM baris b JOIN sales_invoices si ON si.id = b.source_id AND si.tenant_id = p_tenant_id
        WHERE b.reversal_of_id IS NULL AND b.source_type = 'RECEIVE_PAYMENT_UNAPPLY'
        UNION ALL
        -- penerimaan pembayaran: dibagi PROPORSIONAL ke alokasinya; sisa pembulatan ke alokasi id terbesar
        SELECT x.line_id, x.journal_id, x.invoice_id,
               x.bagian + CASE WHEN x.urut = 1 THEN x.net - SUM(x.bagian) OVER (PARTITION BY x.line_id) ELSE 0 END,
               'RECEIVE_PAYMENT'
        FROM (
            SELECT b.line_id, b.journal_id, b.net, rpa.invoice_id,
                   ROUND(b.net * rpa.amount_applied / NULLIF(SUM(rpa.amount_applied) OVER (PARTITION BY b.line_id), 0), 2) AS bagian,
                   ROW_NUMBER() OVER (PARTITION BY b.line_id ORDER BY rpa.id DESC) AS urut
            FROM baris b
            JOIN receive_payments rp ON rp.journal_id = b.journal_id AND rp.tenant_id = p_tenant_id
            JOIN receive_payment_allocations rpa ON rpa.payment_id = rp.id
            WHERE b.reversal_of_id IS NULL AND rpa.amount_applied > 0
        ) x
    ),
    -- pembalik: baris pembalik dipasangkan ke baris asli (akun sama, nominal berlawanan, urutan stabil) dan mewarisi
    -- klaim aslinya DENGAN tanda terbalik. Tanpa pasangan persis -> tak diklaim -> residu (dulu: hilang di kedua sisi).
    pasangan AS (
        SELECT r.line_id AS r_line, r.journal_id AS r_journal, o.line_id AS o_line
        FROM (SELECT b.*, ROW_NUMBER() OVER (PARTITION BY b.journal_id, b.account_id, b.net ORDER BY b.line_id) AS k
              FROM baris b WHERE b.reversal_of_id IS NOT NULL) r
        JOIN (SELECT b.*, ROW_NUMBER() OVER (PARTITION BY b.journal_id, b.account_id, b.net ORDER BY b.line_id) AS k
              FROM baris b) o
          ON o.journal_id = r.reversal_of_id AND o.account_id = r.account_id AND o.net = -r.net AND o.k = r.k
    )
    SELECT a.line_id, a.journal_id, a.invoice_id, a.amount::numeric(18,2), a.cara FROM asal a
    UNION ALL
    SELECT p.r_line, p.r_journal, a.invoice_id, (-a.amount)::numeric(18,2), 'PEMBALIK:' || a.cara
    FROM pasangan p JOIN asal a ON a.line_id = p.o_line
$function$;
