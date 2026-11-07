-- V483 (7 Nov 2026, BACKEND; nomor dipindai dari semua cabang deploy, tertinggi V482): BUG-098 nomor Penerimaan Pembayaran.
-- Putusan MASTER 7 Nov (pola NetSuite/SAP, seragam dgn seri lain DEP-/BP-/INV-YYMM): RCV-YYMM-NNNN, penghitung PER BULAN tanggal bisnis
-- tenant, untuk dokumen BARU saja. Dulu RCV-YYYY-NNNN penghitung per TAHUN (V087/V302) -- terukur 7 Nov: SEMUA tenant (grapgrap 94,
-- kaos 47, nyawit 9). Nomor lama TIDAK diubah (nol UPDATE receive_payments; Law 19/22). Bulan pertama sesudah migrasi mulai 0001;
-- tak bentrok dgn nomor lama (format beda; uq_rcv_payments_tenant_number tetap menjaga).
-- receive_payment_sequences: + year_month (VARCHAR 7, 'YYYY-MM'); baris per tenant tetap SATU (PK tenant_id), reset saat bulan berganti.
-- Cermin rumus Python (routers/receive_payments.py pratinjau) WAJIB ikut di commit kode SESUDAH migrasi ini diterapkan.
-- Penerapan: pemilik LANGSUNG sesudah Selasa. JALANKAN lewat apply_mig.sh (psql -v ON_ERROR_STOP=1 -1; tanpa BEGIN/COMMIT).
-- Pembalik: V483__nomor_rcv_yymm_bulanan_ROLLBACK.sql.

ALTER TABLE receive_payment_sequences ADD COLUMN IF NOT EXISTS year_month VARCHAR(7);

CREATE OR REPLACE FUNCTION public.generate_receive_payment_number(p_tenant_id text)
 RETURNS character varying
 LANGUAGE plpgsql
AS $function$
DECLARE
    v_tgl DATE;
    v_ym VARCHAR(7);
    v_next_number INT;
BEGIN
    v_tgl := tanggal_bisnis(p_tenant_id);
    v_ym := TO_CHAR(v_tgl, 'YYYY-MM');

    INSERT INTO receive_payment_sequences (tenant_id, last_number, year, year_month)
    VALUES (p_tenant_id, 1, EXTRACT(YEAR FROM v_tgl)::int, v_ym)
    ON CONFLICT (tenant_id)
    DO UPDATE SET
        last_number = CASE
            WHEN receive_payment_sequences.year_month = v_ym
            THEN receive_payment_sequences.last_number + 1
            ELSE 1
        END,
        year = EXCLUDED.year,
        year_month = v_ym
    RETURNING last_number INTO v_next_number;

    -- Format: RCV-YYMM-NNNN (BUG-098)
    RETURN 'RCV-' || TO_CHAR(v_tgl, 'YYMM') || '-' || LPAD(v_next_number::TEXT, 4, '0');
END;
$function$;
