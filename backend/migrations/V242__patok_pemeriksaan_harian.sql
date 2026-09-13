-- V242 — tiga pemeriksaan harian yang RUSAK SEJAK LAHIR dihidupkan, dengan patok
--        garis-dasar berbasis IDENTITAS untuk data yang pemilik putuskan dibiarkan.
--
-- Yang rusak (lihat backend/docs/TEMUAN-drift-wac-dan-penjaga-mati-20260913.md):
--   check_7  memanggil compute_ap_adjustments        -> fungsi TAK PERNAH ADA
--   check_9  memanggil compute_inventory_adjustments -> fungsi TAK PERNAH ADA
--            + suku GL asimetris (reversed_by_id IS NULL membuang jurnal asli
--              tapi MENYIMPAN pembaliknya -> artefak 3 juta vs check_15)
--   check_13 SQL kehilangan semua tanda kutip, dan menghadap SATU arah saja
--
-- Kedua fungsi TIDAK dibuat: rumus tanpa fungsi itu sudah menutup sampai rupiah
-- (terukur 13 Sep). Logika dipindah ke DB supaya skrip, gerbang, dan patok
-- memakai SATU definisi himpunan, bukan tiga salinan.
--
-- PATOK = IDENTITAS, BUKAN JUMLAH (meniru V241). Diampuni HANYA bila:
--   sidik jari himpunan anggota SAMA, DAN nilai drift SAMA, DAN
--   jumlah anggota MENUTUP drift (tak ada selisih yang tak terjelaskan).
-- Anggota tambahan, anggota diganti (walau nominal sama), atau drift bergeser
-- -> FAIL_DRIFT_CHANGED.
--
-- Satu tabel per MAKNA: tabel ini BUKAN journal_chain_exemptions (V241).

BEGIN;

CREATE TABLE IF NOT EXISTS health_check_exemptions (
    check_name           text          NOT NULL
        CHECK (check_name IN ('ap_invariant', 'inventory_value', 'status_desync')),
    tenant_id            text          NOT NULL,
    baseline_amount      numeric(18,2) NOT NULL,
    baseline_count       integer       NOT NULL,
    -- md5(string_agg(member_key || ':' || amount, ',' ORDER BY member_key))
    baseline_fingerprint text          NOT NULL,
    reason               text          NOT NULL,
    ticket               text          NOT NULL,
    created_at           timestamptz   NOT NULL DEFAULT now(),
    PRIMARY KEY (check_name, tenant_id)
);

COMMENT ON TABLE health_check_exemptions IS
    'Garis dasar data yang SUDAH DIPUTUSKAN pemilik untuk dibiarkan, per pemeriksaan '
    'harian. Mengampuni yang lama, TIDAK menganggapnya wajar: tiap baris WAJIB '
    'menunjuk tiketnya. Identitas/jumlah/nilai berubah -> FAIL_DRIFT_CHANGED.';

-- ---------------------------------------------------------------------------
-- Gerbang gagal-tertutup (menyalin V188/V241): caller NOBYPASSRLS tanpa lingkup
-- tenant ditolak, bukan dibalas hijau-palsu. Argumen tenant dijadikan lingkup.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION hc_scope_guard(p_tenant text, p_fn text)
RETURNS void LANGUAGE plpgsql AS $$
BEGIN
    IF p_tenant IS NULL OR p_tenant = '' THEN
        RAISE EXCEPTION '%: tenant kosong -- menolak hijau-palsu', p_fn;
    END IF;
    PERFORM set_config('app.tenant_id', p_tenant, true);
END;
$$;

-- ===========================================================================
-- check_7 — AP: GL PAYABLE efektif vs compute_ap_outstanding
-- Anggota = tagihan yang GL-nya (jurnal BILL + pembayaran teralokasi, efektif)
-- tak sama dengan outstanding sub-ledger. Tenant yang memakai kredit/DP vendor
-- bisa punya anggota yang tak menutup drift -> FAIL (gagal-tertutup, sengaja).
-- ===========================================================================
CREATE OR REPLACE FUNCTION hc_ap_members(p_tenant text)
RETURNS TABLE (member_key text, amount numeric)
LANGUAGE plpgsql AS $$
BEGIN
    PERFORM hc_scope_guard(p_tenant, 'hc_ap_members');
    RETURN QUERY
    WITH gl AS (
        SELECT je.id, je.source_type, je.source_id, SUM(jl.credit - jl.debit) AS net
        FROM journal_lines jl
        JOIN journal_entries je ON je.id = jl.journal_id
        JOIN chart_of_accounts coa ON coa.id = jl.account_id
        WHERE coa.account_type = 'PAYABLE'
          AND is_effective_journal(je.id)
          AND je.tenant_id = p_tenant
        GROUP BY je.id, je.source_type, je.source_id
    ),
    sub AS (SELECT s.bill_id, s.outstanding FROM compute_ap_outstanding(p_tenant) s
            WHERE s.bill_id IS NOT NULL),
    per_bill AS (
        SELECT b.id AS bill_id,
            COALESCE((SELECT SUM(g.net) FROM gl g
                      WHERE g.source_type = 'BILL' AND g.source_id::text = b.id::text), 0)
          + COALESCE((SELECT SUM(g.net) FROM gl g
                      JOIN bill_payments_v2 p ON p.journal_id = g.id
                      JOIN bill_payment_allocations a ON a.payment_id = p.id
                      WHERE a.bill_id = b.id), 0) AS gl_net
        FROM bills b WHERE b.tenant_id = p_tenant
    )
    SELECT 'bill:' || pb.bill_id::text,
           (pb.gl_net - COALESCE(s.outstanding, 0))::numeric(18,2)
    FROM per_bill pb
    LEFT JOIN sub s ON s.bill_id = pb.bill_id
    WHERE pb.gl_net <> COALESCE(s.outstanding, 0);
END;
$$;

CREATE OR REPLACE FUNCTION hc_ap_drift(p_tenant text)
RETURNS numeric LANGUAGE plpgsql AS $$
DECLARE v numeric;
BEGIN
    PERFORM hc_scope_guard(p_tenant, 'hc_ap_drift');
    SELECT (
        (SELECT COALESCE(SUM(jl.credit - jl.debit), 0)
         FROM journal_lines jl
         JOIN journal_entries je ON je.id = jl.journal_id
         JOIN chart_of_accounts coa ON coa.id = jl.account_id
         WHERE coa.account_type = 'PAYABLE'
           AND is_effective_journal(je.id)
           AND je.tenant_id = p_tenant)
        - COALESCE((SELECT SUM(outstanding) FROM compute_ap_outstanding(p_tenant)), 0)
    )::numeric(18,2) INTO v;
    RETURN v;
END;
$$;

-- ===========================================================================
-- check_9 — nilai persediaan, akun LITERAL 1-10600 (sengaja beda mekanisme dari
-- check_15 yang berbasis PERAN: dua mekanisme yang sepakat = konfirmasi silang).
-- Suku GL memakai is_effective_journal (asli DAN pembalik sama-sama keluar).
-- Anggota = jurnal efektif ber-1-10600 tanpa satu pun baris ledger (+net)
--         + baris ledger OPENING_BALANCE tanpa jurnal (-nilai).
-- Himpunan ini SEMPIT dengan sengaja; baris relokasi/pembalik produksi tanpa
-- journal_id sah dan saling meniadakan. Kalau suatu saat mereka ikut
-- menyumbang drift, anggota tak lagi menutup drift -> FAIL.
-- ===========================================================================
CREATE OR REPLACE FUNCTION hc_inventory_drift(p_tenant text)
RETURNS numeric LANGUAGE plpgsql AS $$
DECLARE v numeric;
BEGIN
    PERFORM hc_scope_guard(p_tenant, 'hc_inventory_drift');
    SELECT (
        (SELECT COALESCE(SUM(jl.debit - jl.credit), 0)
         FROM journal_lines jl
         JOIN journal_entries je ON je.id = jl.journal_id
         JOIN chart_of_accounts coa ON coa.id = jl.account_id
         WHERE coa.account_code = '1-10600'
           AND is_effective_journal(je.id)
           AND je.tenant_id = p_tenant)
        - (SELECT COALESCE(
               SUM(CASE WHEN il.quantity_in  > 0 THEN il.quantity_in  * il.unit_cost ELSE 0 END)
             - SUM(CASE WHEN il.quantity_out > 0 THEN il.quantity_out * il.unit_cost ELSE 0 END), 0)
           FROM inventory_ledger il WHERE il.tenant_id = p_tenant)
    )::numeric(18,2) INTO v;
    RETURN v;
END;
$$;

CREATE OR REPLACE FUNCTION hc_inventory_members(p_tenant text)
RETURNS TABLE (member_key text, amount numeric)
LANGUAGE plpgsql AS $$
BEGIN
    PERFORM hc_scope_guard(p_tenant, 'hc_inventory_members');
    RETURN QUERY
    SELECT 'journal:' || je.id::text, SUM(jl.debit - jl.credit)::numeric(18,2)
    FROM journal_lines jl
    JOIN journal_entries je ON je.id = jl.journal_id
    JOIN chart_of_accounts coa ON coa.id = jl.account_id
    WHERE coa.account_code = '1-10600'
      AND is_effective_journal(je.id)
      AND je.tenant_id = p_tenant
      AND NOT EXISTS (SELECT 1 FROM inventory_ledger il WHERE il.journal_id = je.id)
    GROUP BY je.id
    HAVING SUM(jl.debit - jl.credit) <> 0
    UNION ALL
    SELECT 'ledger:' || il.id::text,
           (-(COALESCE(il.quantity_in, 0) - COALESCE(il.quantity_out, 0)) * il.unit_cost)::numeric(18,2)
    FROM inventory_ledger il
    WHERE il.tenant_id = p_tenant
      AND il.journal_id IS NULL
      AND il.movement_type = 'OPENING_BALANCE'
      AND (COALESCE(il.quantity_in, 0) - COALESCE(il.quantity_out, 0)) * il.unit_cost <> 0;
END;
$$;

-- ===========================================================================
-- check_13 — status dokumen vs jurnal, DUA ARAH (kutip dipulihkan).
--   arah 1: jurnal hidup, tapi accounting_status <> 'POSTED'
--   arah 2: accounting_status = 'POSTED', jurnalnya sudah dibalik, tanpa jurnal hidup
-- ===========================================================================
CREATE OR REPLACE FUNCTION hc_status_desync_members(p_tenant text)
RETURNS TABLE (member_key text, amount numeric)
LANGUAGE plpgsql AS $$
BEGIN
    PERFORM hc_scope_guard(p_tenant, 'hc_status_desync_members');
    RETURN QUERY
    SELECT 'bill_live_not_posted:' || b.id::text, b.amount::numeric(18,2)
    FROM bills b
    WHERE b.tenant_id = p_tenant AND b.accounting_status <> 'POSTED'
      AND EXISTS (SELECT 1 FROM journal_entries je
                  WHERE je.tenant_id = p_tenant AND je.source_type = 'BILL'
                    AND je.source_id::text = b.id::text
                    AND je.status = 'POSTED' AND je.reversed_by_id IS NULL)
    UNION ALL
    SELECT 'invoice_live_not_posted:' || si.id::text, si.total_amount::numeric(18,2)
    FROM sales_invoices si
    WHERE si.tenant_id = p_tenant AND si.accounting_status <> 'POSTED'
      AND EXISTS (SELECT 1 FROM journal_entries je
                  WHERE je.tenant_id = p_tenant AND je.source_type = 'INVOICE'
                    AND je.source_id::text = si.id::text
                    AND je.status = 'POSTED' AND je.reversed_by_id IS NULL)
    UNION ALL
    SELECT 'bill_posted_journal_reversed:' || b.id::text, b.amount::numeric(18,2)
    FROM bills b
    WHERE b.tenant_id = p_tenant AND b.accounting_status = 'POSTED'
      AND EXISTS (SELECT 1 FROM journal_entries je
                  WHERE je.tenant_id = p_tenant AND je.source_type = 'BILL'
                    AND je.source_id::text = b.id::text AND je.reversed_by_id IS NOT NULL)
      AND NOT EXISTS (SELECT 1 FROM journal_entries je
                      WHERE je.tenant_id = p_tenant AND je.source_type = 'BILL'
                        AND je.source_id::text = b.id::text
                        AND je.status = 'POSTED' AND je.reversed_by_id IS NULL)
    UNION ALL
    SELECT 'invoice_posted_journal_reversed:' || si.id::text, si.total_amount::numeric(18,2)
    FROM sales_invoices si
    WHERE si.tenant_id = p_tenant AND si.accounting_status = 'POSTED'
      AND EXISTS (SELECT 1 FROM journal_entries je
                  WHERE je.tenant_id = p_tenant AND je.source_type = 'INVOICE'
                    AND je.source_id::text = si.id::text AND je.reversed_by_id IS NOT NULL)
      AND NOT EXISTS (SELECT 1 FROM journal_entries je
                      WHERE je.tenant_id = p_tenant AND je.source_type = 'INVOICE'
                        AND je.source_id::text = si.id::text
                        AND je.status = 'POSTED' AND je.reversed_by_id IS NULL);
END;
$$;

-- ===========================================================================
-- Hakim tunggal. drift NULL = pemeriksaan tanpa drift (status_desync): identitas saja.
-- ===========================================================================
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
    ELSE
        RAISE EXCEPTION 'hc_verdict: pemeriksaan tak dikenal %', p_check;
    END IF;

    SELECT * INTO e FROM health_check_exemptions x
     WHERE x.check_name = p_check AND x.tenant_id = p_tenant;

    drift := v_drift; member_count := v_cnt; members_sum := v_sum; fingerprint := v_fp;

    -- sehat: tanpa drift (atau drift 0) DAN tanpa anggota
    IF COALESCE(v_drift, 0) = 0 AND v_cnt = 0 THEN
        verdict := 'PASS';
    ELSIF e.check_name IS NULL THEN
        verdict := 'FAIL_NON_EXEMPT';
    ELSIF v_fp = e.baseline_fingerprint
          AND v_cnt = e.baseline_count
          AND v_sum = e.baseline_amount
          AND (v_drift IS NULL OR v_drift = v_sum)   -- anggota MENUTUP drift
    THEN
        verdict := 'PASS_EXEMPT';
    ELSE
        verdict := 'FAIL_DRIFT_CHANGED';
    END IF;
    RETURN NEXT;
END;
$$;

-- ---------------------------------------------------------------------------
-- Patok dari keadaan HIDUP saat migrasi jalan, bukan id yang diketik.
-- Hanya dipasang bila anggotanya MENUTUP drift -- kalau tidak, tak ada patok
-- dan pemeriksaan tetap merah (lebih baik merah daripada mengampuni yang tak
-- dipahami).
-- ---------------------------------------------------------------------------
INSERT INTO health_check_exemptions
    (check_name, tenant_id, baseline_amount, baseline_count, baseline_fingerprint, reason, ticket)
SELECT 'ap_invariant', 'kaos-biru-konveksi', v.members_sum, v.member_count, v.fingerprint,
       'BILL-2609-0002 (status_v2=void): jurnal BILL-nya dibalik, tapi jurnal PEMBAYARAN '
       '100.000-nya MASIH EFEKTIF. Ini TEMUAN AKUNTANSI NYATA (tagihan batal, bayarnya tidak), '
       'BUKAN artefak. Dipatok karena pemilik memutuskan membiarkan 9 tagihan janggal '
       '(Rp 6.680.000), BUKAN karena dianggap wajar.',
       'TIKET-pembalikan-generik-memutus-ikatan-20260913'
FROM hc_verdict('ap_invariant', 'kaos-biru-konveksi') v
WHERE v.member_count > 0 AND v.drift = v.members_sum;

INSERT INTO health_check_exemptions
    (check_name, tenant_id, baseline_amount, baseline_count, baseline_fingerprint, reason, ticket)
SELECT 'inventory_value', 'kaos-biru-konveksi', v.members_sum, v.member_count, v.fingerprint,
       'Drift −47.994: saldo awal Cm20s-2 128.000 tanpa jurnal + 4 jurnal BILL jasa/maklun '
       'yang mendebit 1-10600 tanpa baris ledger (80.006). Sebabnya pemetaan akun, bukan '
       'penulis ledger. Data dibiarkan atas putusan pemilik; perbaikan jalur = "wajib pilih '
       'barang". Sama sebabnya dengan check_15 (-47.994,31, sisa 0,31 pembulatan WAC).',
       'TEMUAN-drift-wac-dan-penjaga-mati-20260913'
FROM hc_verdict('inventory_value', 'kaos-biru-konveksi') v
WHERE v.member_count > 0 AND v.drift = v.members_sum;

INSERT INTO health_check_exemptions
    (check_name, tenant_id, baseline_amount, baseline_count, baseline_fingerprint, reason, ticket)
SELECT 'status_desync', 'kaos-biru-konveksi', v.members_sum, v.member_count, v.fingerprint,
       '9 tagihan accounting_status=POSTED yang jurnal BILL-nya sudah dibalik tanpa jurnal '
       'hidup (BILL-2609-0002, 0028..0035; Rp 6.680.000). Lahir dari pembalikan generik yang '
       'tak meng-cascade ke dokumen. Pemilik memutuskan dibiarkan; bukan dianggap wajar.',
       'TIKET-pembalikan-generik-memutus-ikatan-20260913'
FROM hc_verdict('status_desync', 'kaos-biru-konveksi') v
WHERE v.member_count > 0;

COMMIT;
