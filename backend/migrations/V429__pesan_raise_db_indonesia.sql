-- V429 (16 Okt 2026, BACKEND; alih dari BACKEND2, putusan MASTER; APPLY hanya sesudah GO pemilik) -- pesan RAISE EXCEPTION
-- fungsi/trigger DB yang bisa sampai ke pengguna Penjualan -> Bahasa Indonesia. HANYA literal pesan yang berubah
-- (CREATE OR REPLACE badan identik; peta & uji: scripts/ops/raise_id.py, gerbang scripts/gate_v429_raise.sh).
-- Tak disentuh: keluarga periode (FE friendlyApiError mencocokkan teks Inggris), salary component (dicocokkan
-- routers/salary_components.py), fungsi pemeriksa kesehatan/seed (bukan ke pengguna). Placeholder % & ERRCODE sama.
DO $jaga$
DECLARE r record; s text;
BEGIN
    -- PENJAGA: tiap fungsi harus PERSIS definisi lama atau baru (md5 pg_get_functiondef). Selain itu = drift -> berhenti.
    FOR r IN SELECT * FROM (VALUES
        ('generate_efaktur_number(text)', '345ea019f66ce7477b3b5d038f3a3b3d', '4aa33b3046b4eb4f6a312dfb3d870567'),
        ('guard_arap_requires_obligation()', '077c879d8f9d62d5d5177819be8c13c8', 'c4251f2407ebca9c04b005dbd06e2803'),
        ('guard_journal_line_header_balance()', 'f80f818893668cc2d53e33ede22a5a74', 'cb2d7dd2bc374a19e1c2172c78553879'),
        ('guard_opening_balance()', '2eb8f9361f132b0c09477d8a70d7313c', '06148a11fa884a6b67de0d2f0c07f9e4'),
        ('prevent_posted_journal_delete()', 'b95726ec19a344b096114f0dbbb4e090', 'cdf958bc80a6fd89b35cc03dad70a879'),
        ('prevent_posted_journal_line_modification()', '17a1960b9d8249617be28719b0032740', 'df0d316681318a327ee74e2476466915'),
        ('prevent_posted_journal_update()', 'f2dfbdfe61c5ffca8f4423ac9bcf7e59', 'd1bc7535cf77ea4a91ca17ac06d62a66'),
        ('prevent_sr_modification()', '884ce6ac49dbee20f717543bd160c8a0', '64c2685896398fd956cd5a17b9559098'),
        ('trg_validate_bill_status_v2()', '4fe69585d9eb50099378159c06ff43c8', '105d6e49e6f96ad03fd3914475847f1a'),
        ('trg_validate_expense_status()', '9cd277395b4bda27a68d5d0b6f16fcb3', '365f049c01a74b4c49aa2d85242b4f35')) v(sig, md5_lama, md5_baru) LOOP
        s := md5(pg_get_functiondef(r.sig::regprocedure));
        IF s NOT IN (r.md5_lama, r.md5_baru) THEN
            RAISE EXCEPTION 'V429: definisi % menyimpang (md5 %) -- bukan lama/baru yang dikenal', r.sig, s;
        END IF;
    END LOOP;
END $jaga$;
CREATE OR REPLACE FUNCTION public.generate_efaktur_number(p_tenant_id text)
 RETURNS character varying
 LANGUAGE plpgsql
AS $function$
DECLARE
    v_seq RECORD;
    v_number BIGINT;
    v_formatted VARCHAR(30);
BEGIN
    -- Get active sequence
    SELECT * INTO v_seq
    FROM efaktur_sequences
    WHERE tenant_id = p_tenant_id AND is_active = true
    ORDER BY created_at DESC
    LIMIT 1
    FOR UPDATE;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'Belum ada rentang nomor e-Faktur yang aktif. Minta alokasi NSFP dari DJP dulu.';
    END IF;

    -- Check if exhausted
    IF v_seq.current_number >= v_seq.range_end THEN
        -- Deactivate current sequence
        UPDATE efaktur_sequences
        SET is_active = false, exhausted_at = NOW()
        WHERE id = v_seq.id;
        RAISE EXCEPTION 'Rentang nomor e-Faktur sudah habis. Minta alokasi NSFP baru dari DJP.';
    END IF;

    -- Increment
    v_number := v_seq.current_number + 1;
    UPDATE efaktur_sequences SET current_number = v_number WHERE id = v_seq.id;

    -- Format: XXX-XXX.XX.XXXXXXXX (total 16 digits + separators)
    -- Example: 010-024.26.00000001
    v_formatted := v_seq.prefix || '.' || LPAD(v_number::TEXT, 8, '0');

    RETURN v_formatted;
END;
$function$
;

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
            RAISE EXCEPTION 'Law 29/30: source_id bukan UUID tidak boleh menyentuh akun Utang. source_type=%, source_id=%', v_source_type, v_source_id;
        END;
        IF NOT v_obligation_exists THEN
            RAISE EXCEPTION 'Law 29/30: akun Utang tidak boleh dikredit tanpa tagihan pembelian. source_type=%, source_id=%', v_source_type, v_source_id;
        END IF;
    END IF;
    IF v_account_type = 'RECEIVABLE' AND NEW.debit > 0 THEN
        BEGIN
            SELECT EXISTS(SELECT 1 FROM sales_invoices WHERE id = v_source_id::uuid) INTO v_obligation_exists;
        EXCEPTION WHEN invalid_text_representation THEN
            RAISE EXCEPTION 'Law 29/30: source_id bukan UUID tidak boleh menyentuh akun Piutang. source_type=%, source_id=%', v_source_type, v_source_id;
        END;
        IF NOT v_obligation_exists THEN
            RAISE EXCEPTION 'Law 29/30: akun Piutang tidak boleh didebit tanpa faktur. source_type=%, source_id=%', v_source_type, v_source_id;
        END IF;
    END IF;
    RETURN NEW;
END;
$function$
;

CREATE OR REPLACE FUNCTION public.guard_journal_line_header_balance()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
DECLARE
    v_journal_id uuid;
    v_status     text;
    v_num        text;
    v_th         numeric(18,2);
    v_tc         numeric(18,2);
    v_sh         numeric(18,2);
    v_sc         numeric(18,2);
BEGIN
    -- Resolve the affected journal id regardless of which table fired.
    IF TG_TABLE_NAME = 'journal_lines' THEN
        v_journal_id := COALESCE(NEW.journal_id, OLD.journal_id);
    ELSE
        v_journal_id := COALESCE(NEW.id, OLD.id);
    END IF;
    IF v_journal_id IS NULL THEN
        RETURN NULL;
    END IF;

    SELECT je.status, je.journal_number, je.total_debit, je.total_credit
      INTO v_status, v_num, v_th, v_tc
      FROM journal_entries je
     WHERE je.id = v_journal_id;

    -- Journal removed within the same transaction, or not POSTED -> nothing to enforce.
    IF NOT FOUND OR v_status IS DISTINCT FROM 'POSTED' THEN
        RETURN NULL;
    END IF;

    SELECT COALESCE(SUM(jl.debit), 0), COALESCE(SUM(jl.credit), 0)
      INTO v_sh, v_sc
      FROM journal_lines jl
     WHERE jl.journal_id = v_journal_id;

    IF ABS(v_th - v_sh) > 0.01 OR ABS(v_tc - v_sc) > 0.01 THEN
        RAISE EXCEPTION
          'Law 4: jurnal % tidak seimbang antara header dan baris: header debit=% kredit=% tetapi jumlah baris debit=% kredit=% (posting ditolak agar buku besar tidak rusak diam-diam)',
          v_num, v_th, v_tc, v_sh, v_sc
          USING ERRCODE = 'check_violation';
    END IF;

    RETURN NULL;
END;
$function$
;

CREATE OR REPLACE FUNCTION public.guard_opening_balance()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
DECLARE
    has_truly_operational BOOLEAN;
BEGIN
    IF NEW.source_type IN ('OPENING', 'OPENING_BALANCE') AND NEW.status = 'POSTED' THEN
        SELECT EXISTS(
            SELECT 1 FROM journal_entries je
            WHERE je.tenant_id = NEW.tenant_id
                AND je.status = 'POSTED'
                AND je.source_type NOT IN (
                    'OPENING', 'OPENING_BALANCE',
                    'OPENING_BALANCE_REVERSAL'
                )
                -- Also exclude reversals OF opening balance journals
                AND NOT (
                    je.source_type = 'REVERSAL'
                    AND EXISTS(
                        SELECT 1 FROM journal_entries orig
                        WHERE orig.id = je.source_id
                        AND orig.source_type IN ('OPENING', 'OPENING_BALANCE')
                    )
                )
        ) INTO has_truly_operational;

        IF has_truly_operational THEN
            RAISE EXCEPTION
                'Saldo awal tidak bisa diposting: usaha % sudah punya transaksi operasional. Balik saldo awal yang ada dulu, lalu posting ulang.',
                NEW.tenant_id;
        END IF;
    END IF;
    RETURN NEW;
END;
$function$
;

CREATE OR REPLACE FUNCTION public.prevent_posted_journal_delete()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
BEGIN
    IF OLD.status IN ('POSTED', 'VOID') THEN
        RAISE EXCEPTION 'Jurnal berstatus % (%) tidak boleh dihapus; riwayat keuangan wajib utuh.',
            OLD.status, OLD.journal_number;
    END IF;
    RETURN OLD;
END;
$function$
;

CREATE OR REPLACE FUNCTION public.prevent_posted_journal_line_modification()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
DECLARE
    v_journal_status TEXT;
    v_journal_number TEXT;
BEGIN
    -- Get journal status
    IF TG_OP = 'DELETE' THEN
        SELECT status, journal_number INTO v_journal_status, v_journal_number
        FROM journal_entries WHERE id = OLD.journal_id;
    ELSE
        SELECT status, journal_number INTO v_journal_status, v_journal_number
        FROM journal_entries WHERE id = NEW.journal_id;
    END IF;

    -- Block modifications if journal is POSTED or VOID
    IF v_journal_status IN ('POSTED', 'VOID') THEN
        RAISE EXCEPTION 'Baris jurnal berstatus % (%) tidak boleh diubah. Batalkan lewat jurnal pembalik.',
            v_journal_status, v_journal_number;
    END IF;

    IF TG_OP = 'DELETE' THEN
        RETURN OLD;
    ELSE
        RETURN NEW;
    END IF;
END;
$function$
;

CREATE OR REPLACE FUNCTION public.prevent_posted_journal_update()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
BEGIN
    IF OLD.status = 'POSTED' THEN
        -- Law 2: jurnal POSTED tak pernah berganti status (void = pembalik + reversed_by_id).
        IF NEW.status IS DISTINCT FROM 'POSTED' THEN
            RAISE EXCEPTION 'Law 2: status jurnal POSTED % tidak boleh berubah menjadi %. Batalkan lewat jurnal pembalik (reversed_by_id).',
                OLD.journal_number, NEW.status
                USING ERRCODE = 'check_violation';
        END IF;

        -- Allow reversed_by_id and reversed_at updates (for reversal linking)
        IF (NEW.reversed_by_id IS DISTINCT FROM OLD.reversed_by_id
            OR NEW.reversed_at IS DISTINCT FROM OLD.reversed_at) THEN
            -- Only allow if other critical fields unchanged
            IF NEW.total_debit = OLD.total_debit
               AND NEW.total_credit = OLD.total_credit
               AND NEW.journal_date = OLD.journal_date
               AND NEW.description = OLD.description
               AND NEW.status = OLD.status THEN
                RETURN NEW;
            END IF;
        END IF;

        -- Block all other modifications to POSTED journals
        IF NEW.total_debit != OLD.total_debit
           OR NEW.total_credit != OLD.total_credit
           OR NEW.journal_date != OLD.journal_date
           OR NEW.description != OLD.description THEN
            RAISE EXCEPTION 'Jurnal POSTED (%) tidak boleh diubah. Batalkan lewat jurnal pembalik.', OLD.journal_number;
        END IF;
    END IF;

    RETURN NEW;
END;
$function$
;

CREATE OR REPLACE FUNCTION public.prevent_sr_modification()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
BEGIN
    IF OLD.status = 'completed' AND TG_OP = 'UPDATE' THEN
        -- Allow only void operation
        IF NEW.status = 'void' THEN
            RETURN NEW;
        END IF;

        RAISE EXCEPTION 'Kwitansi penjualan yang sudah selesai tidak bisa diubah. Batalkan (void) lalu buat yang baru.';
    END IF;

    IF OLD.status = 'void' THEN
        RAISE EXCEPTION 'Kwitansi penjualan yang sudah dibatalkan tidak bisa diubah.';
    END IF;

    RETURN NEW;
END;
$function$
;

CREATE OR REPLACE FUNCTION public.trg_validate_bill_status_v2()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
BEGIN
    -- Skip validation if status_v2 not changed
    IF OLD.status_v2 = NEW.status_v2 THEN
        RETURN NEW;
    END IF;

    -- Validate transition
    IF NOT validate_bill_status_transition(OLD.status_v2, NEW.status_v2) THEN
        RAISE EXCEPTION 'Perubahan status tagihan pembelian dari % ke % tidak diizinkan.',
            OLD.status_v2, NEW.status_v2;
    END IF;

    -- Set posted_at when transitioning to posted
    IF NEW.status_v2 = 'posted' AND OLD.status_v2 = 'draft' THEN
        NEW.posted_at := NOW();
    END IF;

    RETURN NEW;
END;
$function$
;

CREATE OR REPLACE FUNCTION public.trg_validate_expense_status()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
BEGIN
    IF OLD.status = NEW.status THEN
        RETURN NEW;
    END IF;

    IF NOT validate_expense_status_transition(OLD.status, NEW.status) THEN
        RAISE EXCEPTION 'Perubahan status biaya dari % ke % tidak diizinkan.',
            OLD.status, NEW.status;
    END IF;

    RETURN NEW;
END;
$function$
;

