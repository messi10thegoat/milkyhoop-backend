-- V244 — Law 19: nominal dokumen DIBEKUKAN di DB begitu dokumen berjurnal.
--
-- Klaim konstitusi "5 trigger freeze" TERUKUR DEKORATIF dua kali (13 Sep 2026,
-- pg_trigger + pg_proc): nol trigger, nol fungsi. Nominal faktur/tagihan/beban/
-- pembayaran yang sudah dibukukan bisa diubah dengan satu UPDATE.
--
-- PREDIKAT: OLD.journal_id IS NOT NULL — BUKAN accounting_status.
--   accounting_status terukur BERBOHONG (receive_payments 5 posted-berjurnal tapi
--   UNPOSTED; 9 tagihan check_13). journal_id IS NOT NULL <=> jurnal sumber ada:
--   159/159 sepakat, dan jurnalnya sendiri immutable di DB.
--   journal_id IKUT BEKU begitu terisi (13/13 penulisnya bertransisi dari NULL),
--   supaya pagar tak bisa dilepas dengan SET journal_id = NULL.
--
-- PERBANDINGAN NUMERIK (bukan teks jsonb): menulis ulang 100000 di atas 100000.00
-- bukan perubahan nilai dan HARUS lolos.
--
-- ⚠️ BATAS: yang dibekukan HEADER. Tabel BARIS (sales_invoice_items, bill_items,
-- item beban) BELUM — total header bisa tak lagi sama dengan jumlah barisnya dan
-- tak ada yang berbunyi. Unit terpisah. Jangan tulis Law 19 "tutup".
--
-- Kolom yang SENGAJA bebas sesudah posting (turunan/status/metadata, terukur punya
-- penulis sah pasca-posting): amount_paid, status*, fulfillment/revenue, total_cogs,
-- void*, customer/vendor (merge), allocated_amount, unapplied_amount, dll.

BEGIN;

CREATE OR REPLACE FUNCTION law19_bekukan_nominal()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    kol text;
    v_lama text;
    v_baru text;
BEGIN
    -- journal_id sendiri beku begitu terisi
    IF NEW.journal_id IS DISTINCT FROM OLD.journal_id THEN
        RAISE EXCEPTION 'Law 19: %.journal_id sudah terisi dan tak boleh diubah (id %). Batalkan dokumen dan buat dokumen baru.',
            TG_TABLE_NAME, OLD.id
            USING ERRCODE = 'check_violation';
    END IF;

    FOREACH kol IN ARRAY TG_ARGV LOOP
        v_lama := to_jsonb(OLD) ->> kol;
        v_baru := to_jsonb(NEW) ->> kol;
        -- kolom tak ada di baris -> galat berisik (daftar TG_ARGV salah ketik), bukan lolos diam
        IF NOT (to_jsonb(OLD) ? kol) THEN
            RAISE EXCEPTION 'Law 19: kolom % tak ada di % (daftar pagar salah)', kol, TG_TABLE_NAME;
        END IF;
        -- bandingkan sebagai NUMERIC: 100000 == 100000.00
        IF v_lama::numeric IS DISTINCT FROM v_baru::numeric THEN
            RAISE EXCEPTION 'Law 19: nominal %.% dokumen yang sudah dibukukan tak boleh diubah (id %, % -> %). Batalkan dokumen dan buat dokumen baru.',
                TG_TABLE_NAME, kol, OLD.id, v_lama, v_baru
                USING ERRCODE = 'check_violation';
        END IF;
    END LOOP;
    RETURN NEW;
END;
$$;

COMMENT ON FUNCTION law19_bekukan_nominal() IS
    'Law 19 (V244): menolak perubahan kolom nominal (TG_ARGV) dan journal_id pada baris '
    'yang sudah berjurnal. Hanya HEADER; tabel baris belum dipagari.';

DROP TRIGGER IF EXISTS trg_law19_bekukan_nominal ON sales_invoices;
CREATE TRIGGER trg_law19_bekukan_nominal
    BEFORE UPDATE ON sales_invoices
    FOR EACH ROW WHEN (OLD.journal_id IS NOT NULL)
    EXECUTE FUNCTION law19_bekukan_nominal(
        'subtotal', 'discount_percent', 'discount_amount', 'tax_rate', 'tax_amount', 'total_amount');

DROP TRIGGER IF EXISTS trg_law19_bekukan_nominal ON bills;
CREATE TRIGGER trg_law19_bekukan_nominal
    BEFORE UPDATE ON bills
    FOR EACH ROW WHEN (OLD.journal_id IS NOT NULL)
    EXECUTE FUNCTION law19_bekukan_nominal(
        'amount', 'subtotal', 'tax_rate', 'tax_amount', 'dpp', 'dpp_manual', 'grand_total',
        'invoice_discount_percent', 'invoice_discount_amount', 'invoice_discount_total',
        'cash_discount_percent', 'cash_discount_amount', 'cash_discount_total',
        'item_discount_total', 'pph_rate', 'pph_amount', 'pph_dpp');

DROP TRIGGER IF EXISTS trg_law19_bekukan_nominal ON expenses;
CREATE TRIGGER trg_law19_bekukan_nominal
    BEFORE UPDATE ON expenses
    FOR EACH ROW WHEN (OLD.journal_id IS NOT NULL)
    EXECUTE FUNCTION law19_bekukan_nominal(
        'subtotal', 'tax_rate', 'tax_amount', 'pph_rate', 'pph_amount', 'total_amount');

DROP TRIGGER IF EXISTS trg_law19_bekukan_nominal ON receive_payments;
CREATE TRIGGER trg_law19_bekukan_nominal
    BEFORE UPDATE ON receive_payments
    FOR EACH ROW WHEN (OLD.journal_id IS NOT NULL)
    EXECUTE FUNCTION law19_bekukan_nominal('total_amount', 'discount_amount');

DROP TRIGGER IF EXISTS trg_law19_bekukan_nominal ON bill_payments_v2;
CREATE TRIGGER trg_law19_bekukan_nominal
    BEFORE UPDATE ON bill_payments_v2
    FOR EACH ROW WHEN (OLD.journal_id IS NOT NULL)
    EXECUTE FUNCTION law19_bekukan_nominal(
        'total_amount', 'discount_amount', 'bank_fee_amount', 'pph_amount',
        'exchange_rate', 'amount_in_base_currency');

COMMIT;
