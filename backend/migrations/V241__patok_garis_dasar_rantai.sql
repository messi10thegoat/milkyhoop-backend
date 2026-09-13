-- V241 — patok garis-dasar rantai hash: kerusakan LAMA diampuni, kerusakan BARU tetap berbunyi.
--
-- MASALAH: Check 2 (hash chain) melaporkan CRITICAL tiap pagi untuk 2 tautan
-- pecah yang SUDAH ADA (seq 109 & 393, lihat backend/docs/TEMUAN-rantai-nomor-ganda-20260912.md).
-- Menyembuhkannya = menyunting jurnal POSTED (Law 2/3) = putusan pemilik. Sampai
-- itu diputuskan, alarm harian yang selalu merah akan DIABAIKAN -- dan alarm yang
-- diabaikan lebih buruk daripada alarm yang tak ada, karena ia terlihat seperti
-- penjagaan.
--
-- BENTUKNYA MENIRU RUMAH, bukan pola baru: ar_reconciliation_exemptions dan
-- inventory_wac_reconciliation_exemptions + verify_*_all() dengan verdict
-- PASS / PASS_EXEMPT / FAIL_NON_EXEMPT / FAIL_DRIFT_CHANGED.
--
-- SATU TAMBAHAN yang beralasan: SIDIK JARI. Garis dasar skalar (cuma jumlah)
-- BUTA terhadap PENGGANTIAN -- kalau satu pecahan lama sembuh dan satu pecahan
-- BARU muncul, jumlahnya tetap 2 dan gerbang berkata PASS_EXEMPT sambil
-- menyembunyikan kerusakan baru. Sidik jari mengunci IDENTITAS-nya, jadi
-- substitusi ikut memerah.
--
-- YANG SENGAJA TIDAK DIPATOK: check_6_sequence. Ia HIJAU hari ini (0), jadi
-- mematoknya akan membekukan keadaan-sekarang jadi kontrak. Catatan penting
-- tentangnya ada di bawah.

BEGIN;

CREATE TABLE IF NOT EXISTS journal_chain_exemptions (
    tenant_id            text        NOT NULL PRIMARY KEY,
    baseline_broken      integer     NOT NULL DEFAULT 0,
    -- md5 atas journal_id tautan pecah, diurutkan. '' = tak ada yang pecah.
    baseline_fingerprint text        NOT NULL,
    reason               text        NOT NULL,
    ticket               text,
    is_permanent         boolean     NOT NULL DEFAULT false,
    created_at           timestamptz NOT NULL DEFAULT now()
);

COMMENT ON TABLE journal_chain_exemptions IS
    'Garis dasar kerusakan rantai hash yang SUDAH DIKETAHUI. Mengampuni yang lama, '
    'TIDAK menghapusnya. Jumlah ATAU identitas berubah -> FAIL_DRIFT_CHANGED.';
COMMENT ON COLUMN journal_chain_exemptions.baseline_fingerprint IS
    'md5(string_agg(journal_id, '','' ORDER BY journal_id)) atas tautan pecah. '
    'Mengunci IDENTITAS, bukan cuma jumlah, sehingga PENGGANTIAN ikut memerah.';

CREATE OR REPLACE FUNCTION verify_chain_integrity_all()
RETURNS TABLE (
    tenant_id       text,
    broken_count    bigint,
    fingerprint     text,
    baseline_broken integer,
    is_exempt       boolean,
    verdict         text
)
LANGUAGE plpgsql
AS $$
BEGIN
    -- GERBANG GAGAL-TERTUTUP, menyalin V188 (verify_inventory_wac_reconciliation_all):
    -- menolak mengembalikan HIJAU-PALSU saat tak ada lingkup tenant.
    IF NOT COALESCE((SELECT rolbypassrls FROM pg_roles WHERE rolname = current_user), false)
       AND (current_setting('app.tenant_id', true) IS NULL
            OR current_setting('app.tenant_id', true) = '')
    THEN
        RAISE EXCEPTION 'verify_chain_integrity_all: no tenant scope (not BYPASSRLS, app.tenant_id unset) -- refusing to return false-GREEN';
    END IF;

    RETURN QUERY
    WITH tenants AS (
        SELECT DISTINCT je.tenant_id AS tid
        FROM journal_entries je
        WHERE je.status = 'POSTED'
    ),
    per_tenant AS (
        SELECT t.tid,
               COUNT(*) FILTER (WHERE NOT v.is_valid) AS broken,
               COALESCE(
                   md5(string_agg(v.journal_id::text, ',' ORDER BY v.journal_id)
                       FILTER (WHERE NOT v.is_valid)),
                   ''
               ) AS fp
        FROM tenants t
        CROSS JOIN LATERAL verify_chain_integrity(t.tid) v
        GROUP BY t.tid
    )
    SELECT p.tid,
           p.broken,
           p.fp,
           e.baseline_broken,
           (e.tenant_id IS NOT NULL) AS is_exempt,
           CASE
               WHEN e.tenant_id IS NULL AND p.broken = 0 THEN 'PASS'
               WHEN e.tenant_id IS NULL AND p.broken > 0 THEN 'FAIL_NON_EXEMPT'
               -- diampuni HANYA kalau jumlah DAN identitasnya sama persis
               WHEN e.tenant_id IS NOT NULL
                    AND p.broken = e.baseline_broken
                    AND p.fp = e.baseline_fingerprint THEN 'PASS_EXEMPT'
               ELSE 'FAIL_DRIFT_CHANGED'
           END AS verdict
    FROM per_tenant p
    LEFT JOIN journal_chain_exemptions e ON e.tenant_id = p.tid
    ORDER BY (CASE
                WHEN e.tenant_id IS NULL AND p.broken > 0 THEN 0
                WHEN e.tenant_id IS NOT NULL
                     AND (p.broken <> e.baseline_broken
                          OR p.fp <> e.baseline_fingerprint) THEN 0
                ELSE 1 END), p.tid;
END;
$$;

-- Patok garis dasar dari keadaan NYATA saat migrasi jalan -- bukan id yang
-- diketik tangan, supaya tak ada peluang salah ketik yang diam-diam mengampuni
-- kerusakan yang keliru.
INSERT INTO journal_chain_exemptions
    (tenant_id, baseline_broken, baseline_fingerprint, reason, ticket)
SELECT 'kaos-biru-konveksi',
       COUNT(*)::int,
       COALESCE(md5(string_agg(v.journal_id::text, ',' ORDER BY v.journal_id)), ''),
       'Rantai rusak SEBELUM perbaikan Law 2 (84af5e59). Akarnya: void membalik '
       'jurnal asli ke VOID, sehingga penelusur yang menyaring status=POSTED '
       'melompatinya dan previous_hash tak bisa direkonstruksi. Menyembuhkannya '
       'berarti menyunting jurnal POSTED (Law 2/3) = putusan pemilik. '
       'Diampuni AGAR kerusakan BARU tetap terlihat, BUKAN untuk menutupinya.',
       'TEMUAN-rantai-nomor-ganda-20260912'
FROM verify_chain_integrity('kaos-biru-konveksi') v
WHERE NOT v.is_valid
HAVING COUNT(*) > 0
ON CONFLICT (tenant_id) DO NOTHING;

-- ⚠️ CATATAN untuk pembaca berikutnya -- check_6_sequence TIDAK dipatok, dan
-- alasannya perlu diketahui:
--
-- check_6 menghitung chain_sequence ganda dengan saringan status='POSTED'.
-- Terukur 12 Sep 2026: HANYA POSTED -> 0 ganda, SEMUA STATUS -> 9 ganda.
-- Ia HIJAU sementara sembilan pasang rusak duduk di tabel, karena di tiap
-- pasang jurnal ASLI-nya ber-status VOID sehingga tersaring keluar.
-- **Penjaga yang menyaring keluar cacat yang ia jaga.**
--
-- Perbaikan Law 2 (84af5e59) MENGEMBALIKAN penglihatannya: jurnal asli yang
-- dibalik kini TETAP POSTED, jadi tabrakan BARU akan punya dua baris POSTED
-- dan check_6 akan menangkapnya. Sembilan pasang LAMA tetap tak terlihat
-- olehnya -- itu bagian dari putusan data lama yang sama, bukan hal terpisah.
--
-- Karena itu ia dibiarkan TANPA patok: ia hijau dan HARUS tetap bisa memerah.
-- Mematok pemeriksaan yang sedang lulus = membekukan keadaan-sekarang jadi
-- kontrak, dan di sini ia akan membekukan sebuah kebutaan.

COMMIT;
