-- V309 (25 Sep 2026, dogfood grapgrap #1) — backfill STATUS faktur penjualan ke SATU turunan.
--
-- Cacat (kode diperbaiki di commit yang sama): menerapkan DP SEBAGIAN membiarkan status 'posted'
-- (customer_deposits apply: 'paid' bila lunas, selain itu status lama), sedangkan penerimaan
-- pembayaran menulis 'partial'. Kini semua penulis memanggil segarkan_cache_piutang_faktur.
-- Migrasi ini menyamakan baris LAMA dengan aturan helper yang sama:
--   sisa = GREATEST(0, SUM(compute_ar_outstanding.outstanding)) per faktur
--   status = 'paid' bila sisa < 0.01; 'partial' bila (total - sisa) > 0.005; selain itu 'posted'
-- HANYA kolom status (+updated_at) yang diubah — TANPA jurnal, TANPA amount_paid (terukur cocok).
-- Hanya faktur ber-status posted/partial/paid; draf & void tak disentuh.
-- Gagal-keras: bila jumlah/daftar baris yang berubah ≠ yang diukur sebelum jendela, atau ada baris
-- yang amount_paid-nya tak cocok dengan jurnal (status-saja tak cukup) → EXCEPTION, tak ada yang ditulis.
-- Daftar sebelum→sesudah per baris dicetak NOTICE.
DO $$
DECLARE
    r record;
    n int := 0;
    harap text[] := ARRAY['grapgrap-manado/INV-2609-0006','grapgrap-manado/INV-2609-0010','grapgrap-manado/INV-2609-0013','grapgrap-manado/INV-2609-0015','grapgrap-manado/INV-2609-0018','grapgrap-manado/INV-2609-0021','grapgrap-manado/INV-2609-0024','kaos-biru-konveksi/INV-2609-0046','kaos-biru-konveksi/INV-2609-0078'];
    dapat text[] := ARRAY[]::text[];
BEGIN
    CREATE TEMP TABLE v309_rencana ON COMMIT DROP AS
    WITH t AS (SELECT DISTINCT tenant_id FROM sales_invoices WHERE status IN ('posted', 'partial', 'paid')),
    o AS (SELECT t.tenant_id, x.invoice_id, SUM(x.outstanding) AS sisa
          FROM t CROSS JOIN LATERAL compute_ar_outstanding(t.tenant_id) x GROUP BY 1, 2)
    SELECT si.id, si.tenant_id, si.invoice_number, si.status AS status_lama, si.amount_paid, si.total_amount,
           GREATEST(0, COALESCE(o.sisa, 0)) AS sisa,
           CASE WHEN GREATEST(0, COALESCE(o.sisa, 0)) < 0.01 THEN 'paid'
                WHEN si.total_amount - GREATEST(0, COALESCE(o.sisa, 0)) > 0.005 THEN 'partial'
                ELSE 'posted' END AS status_baru
    FROM sales_invoices si
    LEFT JOIN o ON o.tenant_id = si.tenant_id AND o.invoice_id = si.id
    WHERE si.status IN ('posted', 'partial', 'paid');

    FOR r IN SELECT * FROM v309_rencana WHERE status_baru <> status_lama ORDER BY tenant_id, invoice_number LOOP
        IF abs(COALESCE(r.amount_paid, 0) - (r.total_amount - r.sisa)) > 0.005 THEN
            RAISE EXCEPTION 'V309 BATAL: % % amount_paid % ≠ jurnal % (status-saja tak cukup)',
                r.tenant_id, r.invoice_number, r.amount_paid, r.total_amount - r.sisa;
        END IF;
        RAISE NOTICE 'V309 % % : % -> % (dibayar % dari %)', r.tenant_id, r.invoice_number,
            r.status_lama, r.status_baru, r.total_amount - r.sisa, r.total_amount;
        dapat := dapat || (r.tenant_id || '/' || r.invoice_number);
        n := n + 1;
    END LOOP;

    IF (SELECT array_agg(x ORDER BY x) FROM unnest(dapat) x) IS DISTINCT FROM
       (SELECT array_agg(x ORDER BY x) FROM unnest(harap) x) THEN
        RAISE EXCEPTION 'V309 BATAL: baris berubah % ≠ yang diukur %', dapat, harap;
    END IF;

    UPDATE sales_invoices si SET status = p.status_baru, updated_at = NOW()
    FROM v309_rencana p WHERE p.id = si.id AND p.status_baru <> p.status_lama;
    RAISE NOTICE 'V309 LULUS: % faktur disamakan statusnya', n;
END $$;
