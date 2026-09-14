-- V256 (14 Sep 2026) — check_bank_sync_health jadi PEMBUNGKUS TIPIS atas hc_bank_sync_members/drift.
-- Latar: badan lama menaruh je.status='POSTED' di ON-clause LEFT JOIN => DEKORATIF: baris jl VOID
-- ikut terjumlah di journal_balance, saling hapus dgn bank_transaction VOID di txn_balance => gap=0
-- (BUTA, false-GREEN; kelas sama dgn AR buta / V248). Verdict harian sudah TAJAM (hc_bank_sync_drift/
-- members + pin sidik), tapi fungsi ini nol-pemanggil-hidup = ranjau false-GREEN utk pemanggil ad-hoc.
-- Putusan MASTER (B'): SATU sumber rumus. gap per bank = agregasi anggota hc_bank_sync_members per akun;
-- txn_balance = Σ bank_transactions (mentah); journal_balance = txn + gap (= ledger POSTED, identitas
-- POSTED_ledger - Σtxn == Σ anggota). Signature & kolom DIPERTAHANKAN. Lingkup diselaraskan ke hc_
-- (is_active=true). Guard fail-closed V188 dipertahankan.

CREATE OR REPLACE FUNCTION public.check_bank_sync_health(p_tenant_id text DEFAULT NULL::text)
 RETURNS TABLE(tenant_id text, bank_name text, journal_balance numeric, txn_balance numeric, gap numeric, orphan_journals bigint, orphan_bank_txns bigint)
 LANGUAGE plpgsql
AS $function$
BEGIN
    -- FAIL-CLOSED (V188): tolak hijau-palsu bila tak berlingkup.
    IF NOT COALESCE((SELECT rolbypassrls FROM pg_roles WHERE rolname = current_user), false)
       AND (current_setting('app.tenant_id', true) IS NULL OR current_setting('app.tenant_id', true) = '')
       AND (p_tenant_id IS NULL OR p_tenant_id = '')
    THEN
        RAISE EXCEPTION 'check_bank_sync_health: no tenant scope (not BYPASSRLS, app.tenant_id unset, no arg) -- refusing to return false-GREEN';
    END IF;
    IF p_tenant_id IS NOT NULL AND p_tenant_id <> '' THEN
        PERFORM set_config('app.tenant_id', p_tenant_id, true);
    END IF;

    RETURN QUERY
    WITH scope_tenants AS (
        SELECT DISTINCT ba.tenant_id AS t
        FROM bank_accounts ba
        WHERE ba.is_active = true AND ba.coa_id IS NOT NULL
          AND (p_tenant_id IS NULL OR ba.tenant_id = p_tenant_id)
    ),
    -- SATU SUMBER RUMUS: gap per akun = Σ anggota hc_bank_sync_members (bank_account_id ada di member_key).
    mem AS (
        SELECT st.t AS tenant_id,
               split_part(m.member_key, ':', 2) AS bank_account_id,
               SUM(m.amount)::numeric AS gap
        FROM scope_tenants st
        CROSS JOIN LATERAL hc_bank_sync_members(st.t) m
        GROUP BY st.t, split_part(m.member_key, ':', 2)
    ),
    banks AS (
        SELECT ba.id AS bank_account_id, ba.tenant_id, ba.account_name::text AS bank_name, ba.coa_id
        FROM bank_accounts ba
        WHERE ba.is_active = true AND ba.coa_id IS NOT NULL
          AND (p_tenant_id IS NULL OR ba.tenant_id = p_tenant_id)
    ),
    txn AS (
        SELECT bt.bank_account_id, COALESCE(SUM(bt.amount), 0)::numeric AS s
        FROM bank_transactions bt
        GROUP BY bt.bank_account_id
    ),
    oj AS (   -- jurnal POSTED di coa bank tanpa bank_transaction terikat
        SELECT b.bank_account_id, COUNT(DISTINCT je.id) AS cnt
        FROM banks b
        JOIN journal_lines jl ON jl.account_id = b.coa_id
        JOIN journal_entries je ON je.id = jl.journal_id AND je.status = 'POSTED' AND je.tenant_id = b.tenant_id
        LEFT JOIN bank_transactions bt ON bt.journal_id = je.id
        WHERE bt.id IS NULL
        GROUP BY b.bank_account_id
    ),
    obt AS (  -- bank_transactions tanpa journal_id
        SELECT bt.bank_account_id, COUNT(*) AS cnt
        FROM bank_transactions bt
        WHERE bt.journal_id IS NULL
        GROUP BY bt.bank_account_id
    )
    SELECT
        b.tenant_id,
        b.bank_name,
        (COALESCE(t.s, 0) + COALESCE(m.gap, 0))::numeric AS journal_balance,
        COALESCE(t.s, 0)::numeric AS txn_balance,
        COALESCE(m.gap, 0)::numeric AS gap,
        COALESCE(oj.cnt, 0)::bigint AS orphan_journals,
        COALESCE(obt.cnt, 0)::bigint AS orphan_bank_txns
    FROM banks b
    LEFT JOIN mem m ON m.tenant_id = b.tenant_id AND m.bank_account_id = b.bank_account_id::text
    LEFT JOIN txn t ON t.bank_account_id = b.bank_account_id
    LEFT JOIN oj  ON oj.bank_account_id = b.bank_account_id
    LEFT JOIN obt ON obt.bank_account_id = b.bank_account_id
    ORDER BY b.tenant_id, b.bank_name;
END;
$function$;
