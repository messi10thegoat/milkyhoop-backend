-- GERBANG V244 tingkat SQL: G1 (merah skema lama) · G2 (tolak per tabel & per kolom)
-- · T1 (skala beda lolos) · T2 (non-nominal lolos) · G3a (himpunan SET pasca-posting lolos)
-- · G4 (sabotase daftar kolom) · ROLLBACK V244 terbukti. Semua dalam satu transaksi
-- yang di-ROLLBACK. Hasil dicatat dari DALAM handler EXCEPTION (bertahan di transaksi
-- luar) — bukan INSERT yang lalu dibatalkan savepoint (pelajaran V242). Cacah di-assert.
--
-- Pemanggil: BEGIN; \i gerbang (fase LAMA) ; \i V244_badan ; \i gerbang fase BARU ; ROLLBACK
\set ON_ERROR_STOP 1

CREATE TEMP TABLE IF NOT EXISTS hasil (urut serial, sisi text, uji text, hasil text, harap text);

-- coba(sisi, uji, sql, harap): jalankan sql, lalu SELALU batalkan efeknya;
-- catat 'lolos' / 'ditolak: <pesan>'.
CREATE OR REPLACE FUNCTION pg_temp.coba(p_sisi text, p_uji text, p_sql text, p_harap text)
RETURNS void LANGUAGE plpgsql AS $$
DECLARE n bigint;
BEGIN
    BEGIN
        EXECUTE p_sql;
        GET DIAGNOSTICS n = ROW_COUNT;
        RAISE EXCEPTION 'GERBANG_LOLOS:%', n;
    EXCEPTION WHEN OTHERS THEN
        IF SQLERRM LIKE 'GERBANG_LOLOS:%' THEN
            INSERT INTO hasil (sisi, uji, hasil, harap)
            VALUES (p_sisi, p_uji,
                    CASE WHEN SQLERRM = 'GERBANG_LOLOS:0' THEN 'NOL BARIS (stimulus tak sampai)' ELSE 'lolos' END,
                    p_harap);
        ELSE
            INSERT INTO hasil (sisi, uji, hasil, harap)
            VALUES (p_sisi, p_uji, CASE WHEN SQLERRM LIKE 'Law 19:%' THEN 'ditolak' ELSE 'GALAT LAIN: ' || SQLERRM END, p_harap);
        END IF;
    END;
END; $$;

-- satu dokumen BERJURNAL per tabel (dipilih stabil)
CREATE TEMP TABLE IF NOT EXISTS subjek AS
SELECT 'sales_invoices'::text t, (SELECT id FROM sales_invoices WHERE journal_id IS NOT NULL ORDER BY id LIMIT 1) id
UNION ALL SELECT 'bills', (SELECT id FROM bills WHERE journal_id IS NOT NULL ORDER BY id LIMIT 1)
UNION ALL SELECT 'expenses', (SELECT id FROM expenses WHERE journal_id IS NOT NULL ORDER BY id LIMIT 1)
UNION ALL SELECT 'receive_payments', (SELECT id FROM receive_payments WHERE journal_id IS NOT NULL ORDER BY id LIMIT 1)
UNION ALL SELECT 'bill_payments_v2', (SELECT id FROM bill_payments_v2 WHERE journal_id IS NOT NULL ORDER BY id LIMIT 1);

-- satu kolom nominal wakil per tabel
CREATE TEMP TABLE IF NOT EXISTS wakil (t text, kol text);
INSERT INTO wakil SELECT * FROM (VALUES ('sales_invoices','total_amount'), ('bills','grand_total'),
    ('expenses','total_amount'), ('receive_payments','total_amount'), ('bill_payments_v2','total_amount')) v(t,k)
WHERE NOT EXISTS (SELECT 1 FROM wakil);
