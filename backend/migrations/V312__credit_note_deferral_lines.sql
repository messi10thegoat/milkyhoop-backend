-- V312 (26 Sep 2026, BACKEND-2; temuan BACKEND3, putusan MASTER P1+P2+P5) — nota kredit atas pendapatan TERTUNDA.
--
-- Masalah: posting NK = Dr Retur Penjualan / Cr Piutang, TANPA menyentuh Pendapatan Diterima Dimuka maupun
-- sales_invoice_items.allocated_amount. NK (diskon/salah harga/lainnya) atas baris yang barangnya BELUM dikirim
-- (policy 'delivery') -> kewajiban Dimuka terdampar + pendapatan negatif yang tak pernah diakui. Check 16 BUTA
-- (Σ(allocated−recognized) dan GL Dimuka sama-sama tak berubah -> tetap rekonsiliasi). Terbukti di harness journey
-- (skenario_cn_tertunda). Prod 26 Sep: 0 NK terdampak -> tanpa koreksi data.
--
-- Perbaikan (kode credit_notes.py + services/cn_tertunda.py): bagian NK yang jatuh pada kewajiban BELUM dipenuhi
-- (pro-rata (allocated−recognized)/allocated per baris faktur) -> Dr Dimuka, allocated_amount baris turun; sisanya
-- Dr Retur seperti dulu. Tabel ini MENCATAT porsi per baris supaya void NK bisa mencerminkan PERSIS (bukan menebak).
-- Tabel baru saja; tak ada kolom tabel lama yang diubah. Idempoten (IF NOT EXISTS / CREATE OR REPLACE).

CREATE TABLE IF NOT EXISTS credit_note_deferral_lines (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id           text NOT NULL,
    credit_note_id      uuid NOT NULL REFERENCES credit_notes(id),
    invoice_id          uuid NOT NULL REFERENCES sales_invoices(id),
    invoice_item_id     uuid NOT NULL REFERENCES sales_invoice_items(id),
    amount              numeric(18,2) NOT NULL CHECK (amount > 0),
    -- recognized_amount baris SAAT NK diposting: void menolak bila pengakuan bertambah sesudahnya
    recognized_at_cn    numeric(18,2) NOT NULL,
    journal_id          uuid NOT NULL REFERENCES journal_entries(id),
    created_at          timestamptz NOT NULL DEFAULT now(),
    reversed_at         timestamptz,
    reversal_journal_id uuid REFERENCES journal_entries(id)
);
CREATE INDEX IF NOT EXISTS idx_cndl_tenant_cn ON credit_note_deferral_lines (tenant_id, credit_note_id);
CREATE INDEX IF NOT EXISTS idx_cndl_item ON credit_note_deferral_lines (invoice_item_id) WHERE reversed_at IS NULL;

COMMENT ON TABLE credit_note_deferral_lines IS
  'V312: porsi nota kredit yang mendebit Pendapatan Diterima Dimuka per baris faktur (allocated_amount diturunkan sebesar amount). Void NK mengembalikan allocated += amount lalu mengisi reversed_at.';

-- P5 detektor (Check 16 buta terhadap cacat ini). Per tenant ber-NK posted:
--   cn_tanpa_porsi_tertunda : NK posted (reason bukan return/damaged, ada faktur asal) yang faktur asalnya MASIH punya
--                             baris allocated−recognized > 0.005 namun NK itu tak punya porsi tertunda aktif
--                             -> cacat lama berulang / jalur NK baru lupa memecah.
--   cn_selisih_dimuka       : Σ porsi aktif per NK != Σ debit akun REVENUE_DEFERRED di jurnal NK itu.
-- verdict PASS bila keduanya 0. Tenant tanpa NK posted tak muncul (health check membedakan no-data vs BROKEN).
CREATE OR REPLACE FUNCTION verify_cn_deferral_all()
RETURNS TABLE (tenant_id text, cn_posted bigint, cn_tanpa_porsi_tertunda bigint, cn_selisih_dimuka bigint, verdict text)
LANGUAGE sql STABLE AS $$
    WITH cn AS (
        SELECT c.id, c.tenant_id, c.original_invoice_id, c.reason, c.journal_id
        FROM credit_notes c
        WHERE c.status = 'posted' AND c.journal_id IS NOT NULL
    ), dimuka AS (
        SELECT ar.tenant_id, ar.account_id FROM account_roles ar WHERE ar.role_key = 'REVENUE_DEFERRED'
    ), porsi AS (
        SELECT d.credit_note_id, SUM(d.amount) AS amount
        FROM credit_note_deferral_lines d WHERE d.reversed_at IS NULL GROUP BY 1
    ), tanpa AS (
        SELECT cn.tenant_id, cn.id FROM cn
        WHERE cn.original_invoice_id IS NOT NULL
          AND COALESCE(cn.reason, '') NOT IN ('return', 'damaged')
          AND NOT EXISTS (SELECT 1 FROM porsi p WHERE p.credit_note_id = cn.id)
          AND EXISTS (SELECT 1 FROM sales_invoice_items sii
                      WHERE sii.invoice_id = cn.original_invoice_id
                        AND COALESCE(sii.allocated_amount, 0) - COALESCE(sii.recognized_amount, 0) > 0.005)
    ), selisih AS (
        SELECT cn.tenant_id, cn.id FROM cn
        LEFT JOIN porsi p ON p.credit_note_id = cn.id
        WHERE COALESCE(p.amount, 0) <> COALESCE((
            SELECT SUM(jl.debit) FROM journal_lines jl
            JOIN dimuka dm ON dm.account_id = jl.account_id AND dm.tenant_id = cn.tenant_id
            WHERE jl.journal_id = cn.journal_id), 0)
    )
    SELECT t.tenant_id,
           (SELECT COUNT(*) FROM cn WHERE cn.tenant_id = t.tenant_id),
           (SELECT COUNT(*) FROM tanpa WHERE tanpa.tenant_id = t.tenant_id),
           (SELECT COUNT(*) FROM selisih WHERE selisih.tenant_id = t.tenant_id),
           CASE WHEN (SELECT COUNT(*) FROM tanpa WHERE tanpa.tenant_id = t.tenant_id)
                   + (SELECT COUNT(*) FROM selisih WHERE selisih.tenant_id = t.tenant_id) = 0
                THEN 'PASS' ELSE 'FAIL' END
    FROM (SELECT DISTINCT cn.tenant_id FROM cn) t
    ORDER BY 1;
$$;
