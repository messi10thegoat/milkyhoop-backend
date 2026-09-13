-- V248 (14 Sep 2026) — verify_ar_reconciliation_all TIDAK LAGI BUTA terhadap piutang tak teratribusi.
-- Tiket: backend/docs/TIKET-verify-ar-reconciliation-buta-20260913.md. Putusan pemilik 14 Sep 2026:
--   (a) CN-2609-0006 (kaos-biru 25.000) TIDAK dipatok: merahnya dibiarkan terlihat, hilang saat diterapkan (unit B).
--   (b) CN-2608-0001 (grapgrap 200.000) DIPATOK SIDIK (bukan skalar), decided_by = owner.
--   (c) L3 per faktur sekarang.
--
-- Checker lama mengatribusi GL per PELANGGAN lalu MEMBUANG baris yang atribusinya NULL (WHERE customer_id IS NOT NULL),
-- dan is_effective_journal membuang KEDUA sisi pasangan pembalik. Kedua sisi perbandingan membuang hal yang sama ->
-- drift 0 -> PASS palsu (grapgrap GL efektif -180.000 vs compute 20.000 terbaca PASS).
--
-- Rancangan baru — TIDAK berbagi kode dengan compute_ar_outstanding maupun is_effective_journal:
--   ar_klaim_piutang(tenant) : SETIAP baris RECEIVABLE POSTED diklaim ke faktur lewat peta klaim yang ditulis ulang
--                              (INVOICE, CN via original_invoice_id, penerapan DP via journal_id, penerimaan proporsional
--                              per alokasi, pembalik = cermin klaim aslinya bila baris-per-baris saling-nol).
--   L1 total  : GL_POSTED (tanpa saringan efektif) - SUM compute = total_drift
--   L2 residu : himpunan (baris, sisa tak terklaim) HARUS SAMA PERSIS dengan himpunan pin aktif (sidik cocok)
--   L3 faktur : SUM klaim per faktur == outstanding compute per faktur (faktur absen dari compute = 0)
-- Kontrak cron check_14 (monitoring/accounting_health_check.sh:445) DIPERTAHANKAN: kolom sama; PASS / PASS_EXEMPT lulus,
-- verdikt lain gagal. PASS_EXEMPT = residu tepat sama dengan pin. Fungsi tak pernah RAISE karena data merah
-- (hanya penjaga fail-closed V188) -> merah kaos-biru tak menjadikan skrip BROKEN.
-- Tabel ar_reconciliation_exemptions (skalar, kosong) PENSIUN: tak dibaca lagi.

-- ---------------------------------------------------------------- pin sidik
CREATE TABLE ar_reconciliation_pins (
    id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id            text        NOT NULL,
    journal_line_id      uuid        NOT NULL UNIQUE,
    journal_id           uuid        NOT NULL,
    account_id           uuid        NOT NULL,
    net                  numeric(18,2) NOT NULL,
    journal_content_hash text        NOT NULL,
    source_type          text        NOT NULL,
    source_id            uuid        NOT NULL,
    reason               text        NOT NULL,
    ticket               text        NOT NULL,
    decided_by           text        NOT NULL,
    created_at           timestamptz NOT NULL DEFAULT now()
);
COMMENT ON TABLE ar_reconciliation_pins IS
  'V248: selisih AR historis yang DIPUTUSKAN dibiarkan, dipatok per BARIS jurnal dengan sidik (akun, nominal, hash konten, sumber). Pin yang tak lagi cocok = FAIL_STALE_PIN, bukan lulus diam.';

-- ---------------------------------------------------------------- peta klaim independen
CREATE OR REPLACE FUNCTION ar_klaim_piutang(p_tenant_id text)
RETURNS TABLE(line_id uuid, journal_id uuid, invoice_id uuid, amount numeric, cara text)
LANGUAGE sql STABLE AS $$
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
$$;

-- ---------------------------------------------------------------- rincian (L2 residu + pin, L3 faktur)
CREATE OR REPLACE FUNCTION verify_ar_reconciliation_rincian(p_tenant_id text)
RETURNS TABLE(lapis text, kunci uuid, nomor text, nilai numeric, keterangan text)
LANGUAGE sql STABLE AS $$
    WITH baris AS (
        SELECT jl.id AS line_id, je.id AS journal_id, je.journal_number, je.source_type, je.source_id, je.content_hash,
               jl.account_id, (jl.debit - jl.credit)::numeric(18,2) AS net
        FROM journal_lines jl
        JOIN journal_entries je ON je.id = jl.journal_id
        JOIN chart_of_accounts coa ON coa.id = jl.account_id
        WHERE je.tenant_id = p_tenant_id AND je.status = 'POSTED' AND coa.account_type = 'RECEIVABLE'
    ),
    klaim AS (SELECT * FROM ar_klaim_piutang(p_tenant_id)),
    residu AS (
        SELECT b.*, (b.net - COALESCE((SELECT SUM(k.amount) FROM klaim k WHERE k.line_id = b.line_id), 0))::numeric(18,2) AS sisa
        FROM baris b
    ),
    pin AS (
        SELECT p.*, r.sisa,
               (r.line_id IS NOT NULL AND r.journal_id = p.journal_id AND r.account_id = p.account_id AND r.net = p.net
                AND r.content_hash = p.journal_content_hash AND r.source_type = p.source_type AND r.source_id = p.source_id
                AND r.sisa = p.net) AS cocok
        FROM ar_reconciliation_pins p LEFT JOIN residu r ON r.line_id = p.journal_line_id
        WHERE p.tenant_id = p_tenant_id
    ),
    faktur AS (
        SELECT f.invoice_id,
               COALESCE((SELECT SUM(k.amount) FROM klaim k WHERE k.invoice_id = f.invoice_id), 0)::numeric(18,2) AS klaim,
               COALESCE((SELECT SUM(o.outstanding) FROM compute_ar_outstanding(p_tenant_id) o WHERE o.invoice_id = f.invoice_id), 0)::numeric(18,2) AS kanonik
        FROM (SELECT k.invoice_id FROM klaim k UNION SELECT o.invoice_id FROM compute_ar_outstanding(p_tenant_id) o) f
    )
    SELECT 'L2_RESIDU_TERPATOK', r.line_id, r.journal_number, r.sisa, r.source_type || ' ' || r.source_id
    FROM residu r WHERE r.sisa <> 0 AND EXISTS (SELECT 1 FROM pin p WHERE p.journal_line_id = r.line_id AND p.cocok)
    UNION ALL
    SELECT 'L2_RESIDU_TAK_TERPATOK', r.line_id, r.journal_number, r.sisa, r.source_type || ' ' || r.source_id
    FROM residu r WHERE r.sisa <> 0 AND NOT EXISTS (SELECT 1 FROM pin p WHERE p.journal_line_id = r.line_id AND p.cocok)
    UNION ALL
    SELECT 'L2_PIN_BASI', p.journal_line_id, NULL, p.net, 'pin ' || p.ticket || ': sidik tak cocok / sudah terklaim (sisa=' || COALESCE(p.sisa::text, 'baris hilang') || ')'
    FROM pin p WHERE NOT p.cocok
    UNION ALL
    SELECT 'L3_FAKTUR', fk.invoice_id, (SELECT si.invoice_number FROM sales_invoices si WHERE si.id = fk.invoice_id),
           fk.klaim - fk.kanonik, 'klaim GL=' || fk.klaim || ' compute=' || fk.kanonik
    FROM faktur fk WHERE fk.klaim <> fk.kanonik
$$;

-- ---------------------------------------------------------------- pengganti verify_ar_reconciliation_all (kontrak kolom sama)
CREATE OR REPLACE FUNCTION public.verify_ar_reconciliation_all()
 RETURNS TABLE(tenant_id text, total_canonical numeric, total_gl numeric, total_drift numeric, is_exempt boolean, baseline_drift numeric, verdict text)
 LANGUAGE plpgsql
 STABLE
AS $function$
BEGIN
    -- FAIL-CLOSED gate (V188): refuse to return a false-GREEN.
    IF NOT COALESCE((SELECT rolbypassrls FROM pg_roles WHERE rolname = current_user), false)
       AND (current_setting('app.tenant_id', true) IS NULL OR current_setting('app.tenant_id', true) = '')
    THEN
        RAISE EXCEPTION 'verify_ar_reconciliation_all: no tenant scope (not BYPASSRLS, app.tenant_id unset) -- refusing to return false-GREEN';
    END IF;

    RETURN QUERY
    WITH tenants AS (
        SELECT DISTINCT je.tenant_id AS tid
        FROM journal_entries je
        JOIN journal_lines jl ON jl.journal_id = je.id
        JOIN chart_of_accounts coa ON coa.id = jl.account_id
        WHERE coa.account_type = 'RECEIVABLE'
        UNION
        SELECT p.tenant_id FROM ar_reconciliation_pins p
    ),
    per_tenant AS (
        SELECT t.tid,
               (SELECT COALESCE(SUM(o.outstanding), 0) FROM compute_ar_outstanding(t.tid) o)::numeric(18,2) AS canon,
               (SELECT COALESCE(SUM(jl.debit - jl.credit), 0) FROM journal_lines jl
                  JOIN journal_entries je ON je.id = jl.journal_id
                  JOIN chart_of_accounts coa ON coa.id = jl.account_id
                 WHERE je.tenant_id = t.tid AND je.status = 'POSTED' AND coa.account_type = 'RECEIVABLE')::numeric(18,2) AS gl,
               (SELECT COALESCE(SUM(p.net), 0) FROM ar_reconciliation_pins p WHERE p.tenant_id = t.tid)::numeric(18,2) AS pin_net,
               EXISTS (SELECT 1 FROM ar_reconciliation_pins p WHERE p.tenant_id = t.tid) AS ada_pin
        FROM tenants t
    ),
    temuan AS (
        SELECT t.tid,
               count(*) FILTER (WHERE r.lapis = 'L2_PIN_BASI') AS basi,
               count(*) FILTER (WHERE r.lapis = 'L2_RESIDU_TAK_TERPATOK') AS tak_terpatok,
               count(*) FILTER (WHERE r.lapis = 'L3_FAKTUR') AS faktur
        FROM tenants t LEFT JOIN LATERAL verify_ar_reconciliation_rincian(t.tid) r ON TRUE
        GROUP BY t.tid
    )
    SELECT p.tid, p.canon, p.gl, (p.gl - p.canon)::numeric(18,2), p.ada_pin, p.pin_net,
           CASE
               WHEN f.basi > 0 THEN 'FAIL_STALE_PIN'
               WHEN f.tak_terpatok > 0 THEN 'FAIL_UNPINNED'
               WHEN f.faktur > 0 THEN 'FAIL_PER_INVOICE'
               WHEN ABS(p.gl - p.canon - p.pin_net) > 0.01 THEN 'FAIL_TOTAL'   -- L1: tak mungkin bila L2+L3 hijau; penjaga aritmetika
               WHEN p.ada_pin THEN 'PASS_EXEMPT'
               ELSE 'PASS'
           END
    FROM per_tenant p JOIN temuan f ON f.tid = p.tid
    ORDER BY p.tid;
END;
$function$;

COMMENT ON FUNCTION verify_ar_reconciliation(text) IS
  'DIGANTIKAN V248: buta terhadap GL piutang tak teratribusi (baris atribusi NULL dibuang). Tak dipakai verify_ar_reconciliation_all lagi; lihat verify_ar_reconciliation_rincian.';

-- ---------------------------------------------------------------- pin CN-2608-0001 (putusan pemilik), compare-and-set
DO $$
DECLARE
    v record;
    n int;
BEGIN
    SELECT count(*) INTO n
    FROM journal_lines jl JOIN journal_entries je ON je.id = jl.journal_id JOIN chart_of_accounts coa ON coa.id = jl.account_id
    JOIN credit_notes cn ON cn.id = je.source_id AND je.source_type = 'CREDIT_NOTE'
    WHERE cn.tenant_id = 'grapgrap-manado' AND cn.credit_note_number = 'CN-2608-0001' AND coa.account_type = 'RECEIVABLE' AND je.status = 'POSTED';
    IF n <> 1 THEN
        RAISE EXCEPTION 'V248: baris piutang CN-2608-0001 harus tepat 1, ditemukan %', n;
    END IF;

    SELECT jl.id AS line_id, je.id AS journal_id, jl.account_id, (jl.debit - jl.credit)::numeric(18,2) AS net,
           je.content_hash, je.source_type, je.source_id, je.reversed_by_id, cn.original_invoice_id, cn.status
      INTO v
    FROM journal_lines jl JOIN journal_entries je ON je.id = jl.journal_id JOIN chart_of_accounts coa ON coa.id = jl.account_id
    JOIN credit_notes cn ON cn.id = je.source_id AND je.source_type = 'CREDIT_NOTE'
    WHERE cn.tenant_id = 'grapgrap-manado' AND cn.credit_note_number = 'CN-2608-0001' AND coa.account_type = 'RECEIVABLE' AND je.status = 'POSTED';

    IF v.line_id <> '4e15a29a-7c67-48bb-8067-df6c5bcf7f90'::uuid OR v.net <> -200000.00 OR v.content_hash IS NULL
       OR v.reversed_by_id IS NOT NULL OR v.original_invoice_id IS NOT NULL OR v.status <> 'posted' THEN
        RAISE EXCEPTION 'V248: sidik CN-2608-0001 tak sesuai pengukuran 14 Sep (line %, net %, hash null %, dibalik %, faktur %, status %)',
            v.line_id, v.net, v.content_hash IS NULL, v.reversed_by_id IS NOT NULL, v.original_invoice_id, v.status;
    END IF;

    INSERT INTO ar_reconciliation_pins (tenant_id, journal_line_id, journal_id, account_id, net, journal_content_hash,
                                        source_type, source_id, reason, ticket, decided_by)
    VALUES ('grapgrap-manado', v.line_id, v.journal_id, v.account_id, v.net, v.content_hash, v.source_type, v.source_id,
            'CN-2608-0001 (Toko Melati, 200.000) tanpa faktur asal; satu-satunya faktur Toko Melati yang bersisa hanya 20.000 sehingga tak bisa diterapkan (unit B). Pemilik memutuskan dibiarkan apa adanya: kredit benar di buku besar, dampak hanya selisih laporan per faktur.',
            'TIKET-verify-ar-reconciliation-buta-20260913.md', 'owner');

    INSERT INTO audit_logs (id, "eventType", entity_type, entity_id, entity_number, tenant_id, source, metadata, success, "createdAt")
    VALUES (gen_random_uuid()::text, 'AR_RECONCILIATION_PIN', 'journal_line', v.line_id, 'CN-2608-0001',
            'grapgrap-manado', 'migration:V248',
            jsonb_build_object('decided_by', 'owner', 'keputusan_tanggal', '2026-09-14',
                               'ticket', 'TIKET-verify-ar-reconciliation-buta-20260913.md',
                               'sidik', jsonb_build_object('journal_id', v.journal_id, 'account_id', v.account_id, 'net', v.net,
                                                           'content_hash', v.content_hash, 'source_type', v.source_type, 'source_id', v.source_id)),
            true, now());
END $$;
