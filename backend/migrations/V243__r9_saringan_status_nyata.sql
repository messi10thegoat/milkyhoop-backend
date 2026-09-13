-- V243 — R9 (check_3) dibetulkan: saringan status jurnal DITEGAKKAN, data lama DIPATOK.
--
-- Putusan pemilik 13 Sep 2026: "R9 pilih A, betulkan dan patok".
-- Lihat backend/docs/TIKET-r9-saringan-status-dekoratif-20260913.md.
--
-- CACATNYA: kueri R9 lama menaruh je.status='POSTED' di dalam ON sebuah LEFT JOIN.
-- Syarat itu menolkan kolom je, TIDAK membuang baris journal_lines -> R9 menjumlahkan
-- jurnal berstatus APA PUN. Hijaunya hari ini lahir dari 16 jurnal VOID lama yang ikut
-- terhitung; jurnal DRAFT ber-btx terikat akan TERSEMBUNYI sepenuhnya.
--
-- SEKARANG: saldo buku = hanya jurnal POSTED (syarat di WHERE, bukan ON).
-- Anggota per rekening bank aktif:
--   'journal:<rekening>:<jurnal>'  jurnal non-POSTED yang punya btx terikat ke rekening
--                                   itu; nilai = -(net baris jurnal di CoA rekening)
--   'residual:<rekening>'          sisa gap yang TAK dijelaskan anggota di atas (bila ≠0)
-- Jadi setiap gap PASTI punya identitas; sisa tak terjelaskan ikut sidik jari -> merah.
--
-- Patok = IDENTITAS (meniru V242): 16 jurnal VOID lama di dua rekening. Jurnal non-POSTED
-- BARU, jurnal diganti walau nominal sama, atau sisa baru -> FAIL_DRIFT_CHANGED.
--
-- Satu tabel per MAKNA tetap dijaga: bank_sync = patok pemeriksaan harian, sama makna
-- dengan V242 (bukan patok rantai V241).

BEGIN;

ALTER TABLE health_check_exemptions DROP CONSTRAINT health_check_exemptions_check_name_check;
ALTER TABLE health_check_exemptions ADD CONSTRAINT health_check_exemptions_check_name_check
    CHECK (check_name IN ('ap_invariant', 'inventory_value', 'status_desync', 'bank_sync'));

CREATE OR REPLACE FUNCTION hc_bank_sync_members(p_tenant text)
RETURNS TABLE (member_key text, amount numeric)
LANGUAGE plpgsql AS $$
BEGIN
    PERFORM hc_scope_guard(p_tenant, 'hc_bank_sync_members');
    RETURN QUERY
    WITH rek AS (
        SELECT ba.id AS bank_account_id, ba.coa_id
        FROM bank_accounts ba
        WHERE ba.tenant_id = p_tenant AND ba.is_active = true
    ),
    gap AS (
        SELECT r.bank_account_id,
               (COALESCE((SELECT SUM(jl.debit - jl.credit)
                          FROM journal_lines jl
                          JOIN journal_entries je ON je.id = jl.journal_id
                          WHERE jl.account_id = r.coa_id
                            AND je.tenant_id = p_tenant
                            AND je.status = 'POSTED'), 0)          -- syarat di WHERE: NYATA
              - COALESCE((SELECT SUM(bt.amount)
                          FROM bank_transactions bt
                          WHERE bt.bank_account_id = r.bank_account_id
                            AND bt.tenant_id = p_tenant), 0))::numeric(18,2) AS gap
        FROM rek r
    ),
    jurnal_non_posted AS (
        SELECT r.bank_account_id, je.id AS journal_id,
               (-SUM(jl.debit - jl.credit))::numeric(18,2) AS amount
        FROM rek r
        JOIN journal_lines jl ON jl.account_id = r.coa_id
        JOIN journal_entries je ON je.id = jl.journal_id
        WHERE je.tenant_id = p_tenant
          AND je.status <> 'POSTED'
          AND EXISTS (SELECT 1 FROM bank_transactions bt
                      WHERE bt.journal_id = je.id AND bt.bank_account_id = r.bank_account_id)
        GROUP BY r.bank_account_id, je.id
        HAVING SUM(jl.debit - jl.credit) <> 0
    ),
    sisa AS (
        SELECT g.bank_account_id,
               (g.gap - COALESCE((SELECT SUM(j.amount) FROM jurnal_non_posted j
                                  WHERE j.bank_account_id = g.bank_account_id), 0))::numeric(18,2) AS amount
        FROM gap g
    )
    SELECT 'journal:' || j.bank_account_id::text || ':' || j.journal_id::text, j.amount
    FROM jurnal_non_posted j
    UNION ALL
    SELECT 'residual:' || s.bank_account_id::text, s.amount
    FROM sisa s WHERE s.amount <> 0;
END;
$$;

CREATE OR REPLACE FUNCTION hc_bank_sync_drift(p_tenant text)
RETURNS numeric LANGUAGE plpgsql AS $$
DECLARE v numeric;
BEGIN
    PERFORM hc_scope_guard(p_tenant, 'hc_bank_sync_drift');
    SELECT COALESCE(SUM(
        COALESCE((SELECT SUM(jl.debit - jl.credit)
                  FROM journal_lines jl JOIN journal_entries je ON je.id = jl.journal_id
                  WHERE jl.account_id = ba.coa_id AND je.tenant_id = p_tenant
                    AND je.status = 'POSTED'), 0)
      - COALESCE((SELECT SUM(bt.amount) FROM bank_transactions bt
                  WHERE bt.bank_account_id = ba.id AND bt.tenant_id = p_tenant), 0)
    ), 0)::numeric(18,2) INTO v
    FROM bank_accounts ba
    WHERE ba.tenant_id = p_tenant AND ba.is_active = true;
    RETURN v;
END;
$$;

-- hc_verdict: cabang V242 disalin UTUH, satu cabang 'bank_sync' ditambahkan.
CREATE OR REPLACE FUNCTION hc_verdict(p_check text, p_tenant text)
RETURNS TABLE (drift numeric, member_count integer, members_sum numeric,
               fingerprint text, verdict text)
LANGUAGE plpgsql AS $$
DECLARE
    v_drift numeric;
    v_cnt integer;
    v_sum numeric;
    v_fp text;
    e health_check_exemptions%ROWTYPE;
BEGIN
    PERFORM hc_scope_guard(p_tenant, 'hc_verdict');

    IF p_check = 'ap_invariant' THEN
        v_drift := hc_ap_drift(p_tenant);
        SELECT count(*), COALESCE(sum(m.amount), 0),
               COALESCE(md5(string_agg(m.member_key || ':' || m.amount::text, ',' ORDER BY m.member_key)), '')
          INTO v_cnt, v_sum, v_fp FROM hc_ap_members(p_tenant) m;
    ELSIF p_check = 'inventory_value' THEN
        v_drift := hc_inventory_drift(p_tenant);
        SELECT count(*), COALESCE(sum(m.amount), 0),
               COALESCE(md5(string_agg(m.member_key || ':' || m.amount::text, ',' ORDER BY m.member_key)), '')
          INTO v_cnt, v_sum, v_fp FROM hc_inventory_members(p_tenant) m;
    ELSIF p_check = 'status_desync' THEN
        v_drift := NULL;
        SELECT count(*), COALESCE(sum(m.amount), 0),
               COALESCE(md5(string_agg(m.member_key || ':' || m.amount::text, ',' ORDER BY m.member_key)), '')
          INTO v_cnt, v_sum, v_fp FROM hc_status_desync_members(p_tenant) m;
    ELSIF p_check = 'bank_sync' THEN
        v_drift := hc_bank_sync_drift(p_tenant);
        SELECT count(*), COALESCE(sum(m.amount), 0),
               COALESCE(md5(string_agg(m.member_key || ':' || m.amount::text, ',' ORDER BY m.member_key)), '')
          INTO v_cnt, v_sum, v_fp FROM hc_bank_sync_members(p_tenant) m;
    ELSE
        RAISE EXCEPTION 'hc_verdict: pemeriksaan tak dikenal %', p_check;
    END IF;

    SELECT * INTO e FROM health_check_exemptions x
     WHERE x.check_name = p_check AND x.tenant_id = p_tenant;

    drift := v_drift; member_count := v_cnt; members_sum := v_sum; fingerprint := v_fp;

    IF COALESCE(v_drift, 0) = 0 AND v_cnt = 0 THEN
        verdict := 'PASS';
    ELSIF e.check_name IS NULL THEN
        verdict := 'FAIL_NON_EXEMPT';
    ELSIF v_fp = e.baseline_fingerprint
          AND v_cnt = e.baseline_count
          AND v_sum = e.baseline_amount
          AND (v_drift IS NULL OR v_drift = v_sum)
    THEN
        verdict := 'PASS_EXEMPT';
    ELSE
        verdict := 'FAIL_DRIFT_CHANGED';
    END IF;
    RETURN NEXT;
END;
$$;

-- Patok dari keadaan HIDUP, HANYA bila semua anggota adalah jurnal (nol sisa
-- tak terjelaskan). Ada sisa -> tak dipatok, tetap merah.
INSERT INTO health_check_exemptions
    (check_name, tenant_id, baseline_amount, baseline_count, baseline_fingerprint, reason, ticket)
SELECT 'bank_sync', 'kaos-biru-konveksi', v.members_sum, v.member_count, v.fingerprint,
       '16 jurnal asli beban berstatus VOID yang tetap punya bank_transaction terikat '
       '(BCA Operasional 6 jurnal -51.848; Bendahara 10 jurnal -13.370). Lahir dari void '
       'yang membalik jurnal asli ke VOID SEBELUM perbaikan Law 2 (84af5e59). R9 lama '
       'hijau karena saringan statusnya dekoratif dan ikut menjumlahkan jurnal ini. '
       'Pemilik memutuskan data dibiarkan; dipatok agar gap BARU terlihat, BUKAN karena wajar.',
       'TIKET-r9-saringan-status-dekoratif-20260913'
FROM hc_verdict('bank_sync', 'kaos-biru-konveksi') v
WHERE v.member_count > 0
  AND NOT EXISTS (SELECT 1 FROM hc_bank_sync_members('kaos-biru-konveksi') m
                  WHERE m.member_key LIKE 'residual:%');

COMMIT;
