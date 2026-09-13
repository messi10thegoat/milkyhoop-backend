-- V247 — credit_notes.customer_id & customer_deposits.customer_id: VARCHAR(255) -> UUID + FK KOMPOSIT ke customers.
--
-- Putusan pemilik 13 Sep 2026 (seragamkan ke uuid, SESUDAH pembuat divalidasi 3661597c dan data dibetulkan V247a).
-- Kelas cacat yang ditutup di akarnya: kolom pihak uuid di 31 tabel, varchar di 2 -> 5 fitur rusak hari ini
-- (terapkan CN, DP otomatis lebih bayar, tab jurnal pelanggan, ubah draf CN, + tambalan satu-per-satu lama).
--
-- FK KOMPOSIT (customer_id, tenant_id) -> customers(id, tenant_id), bukan FK sederhana: "pelanggan satu tenant dgn
-- dokumennya" adalah predikat atas baris -> ditegakkan di DB (Law 13). FK sederhana hanya menjamin pelanggan ADA.
-- Butuh UNIQUE(id, tenant_id) di customers (secara logika pasti unik; id = PK). ON DELETE NO ACTION: hapus pelanggan
-- terukur SOFT (deleted_at/is_active), sama dgn preseden FK sales_invoices/receive_payments.
--
-- WAJIB sesudah DEPLOY 1 (kode dwi-kompatibel) hidup; tanpa itu daftar/detail DP, tab jurnal pelanggan, dan merge
-- pecah, dan terapkan nota kredit terbuka diam-diam.

BEGIN;

DO $$
DECLARE n integer;
BEGIN
    SELECT count(*) INTO n FROM credit_notes
     WHERE customer_id IS NOT NULL AND btrim(customer_id) <> ''
       AND btrim(customer_id) !~* '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$';
    IF n > 0 THEN RAISE EXCEPTION 'V247: credit_notes punya % customer_id bukan UUID', n; END IF;
    SELECT count(*) INTO n FROM customer_deposits
     WHERE customer_id IS NOT NULL AND btrim(customer_id) <> ''
       AND btrim(customer_id) !~* '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$';
    IF n > 0 THEN RAISE EXCEPTION 'V247: customer_deposits punya % customer_id bukan UUID', n; END IF;

    SELECT count(*) INTO n FROM credit_notes x
     WHERE NULLIF(btrim(x.customer_id), '') IS NOT NULL
       AND NOT EXISTS (SELECT 1 FROM customers c WHERE c.id = btrim(x.customer_id)::uuid AND c.tenant_id = x.tenant_id);
    IF n > 0 THEN RAISE EXCEPTION 'V247: credit_notes punya % pelanggan tak ada / beda tenant', n; END IF;
    SELECT count(*) INTO n FROM customer_deposits x
     WHERE NULLIF(btrim(x.customer_id), '') IS NOT NULL
       AND NOT EXISTS (SELECT 1 FROM customers c WHERE c.id = btrim(x.customer_id)::uuid AND c.tenant_id = x.tenant_id);
    IF n > 0 THEN RAISE EXCEPTION 'V247: customer_deposits punya % pelanggan tak ada / beda tenant', n; END IF;

    SELECT count(*) INTO n FROM customer_deposits WHERE customer_id = '';
    IF n > 0 THEN RAISE EXCEPTION 'V247: customer_deposits punya % string kosong (ROLLBACK tak akan lossless)', n; END IF;
    SELECT count(*) INTO n FROM credit_notes WHERE customer_id = '';
    IF n > 0 THEN RAISE EXCEPTION 'V247: credit_notes punya % string kosong (ROLLBACK tak akan lossless)', n; END IF;
    SELECT count(*) INTO n FROM (
        SELECT customer_id FROM credit_notes WHERE customer_id IS NOT NULL AND customer_id <> lower(btrim(customer_id))
        UNION ALL
        SELECT customer_id FROM customer_deposits WHERE customer_id IS NOT NULL AND customer_id <> lower(btrim(customer_id))) s;
    IF n > 0 THEN RAISE EXCEPTION 'V247: % nilai tak kanonik (huruf besar/spasi) -> ROLLBACK tak lossless', n; END IF;
END $$;

ALTER TABLE customers ADD CONSTRAINT uq_customers_id_tenant UNIQUE (id, tenant_id);

ALTER TABLE credit_notes      ALTER COLUMN customer_id TYPE uuid USING NULLIF(btrim(customer_id), '')::uuid;
ALTER TABLE customer_deposits ALTER COLUMN customer_id TYPE uuid USING NULLIF(btrim(customer_id), '')::uuid;

ALTER TABLE credit_notes ADD CONSTRAINT fk_credit_notes_customer_tenant
    FOREIGN KEY (customer_id, tenant_id) REFERENCES customers (id, tenant_id) ON DELETE NO ACTION;
ALTER TABLE customer_deposits ADD CONSTRAINT fk_customer_deposits_customer_tenant
    FOREIGN KEY (customer_id, tenant_id) REFERENCES customers (id, tenant_id) ON DELETE NO ACTION;

COMMIT;
