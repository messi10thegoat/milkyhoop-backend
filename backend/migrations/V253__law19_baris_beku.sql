-- V253 (14 Sep 2026) — Law 19 baris: bekukan kolom NOMINAL baris dokumen sesudah dokumen dibukukan (journal_id induk
-- terisi). Melengkapi V244 (header). Tanpa ini: header beku tapi baris bisa diubah -> total header != Σ baris, senyap.
-- HANYA kolom nominal. Kolom PROGRES (fulfilled_qty, recognized_amount, allocated_amount) & IDENTITAS (batch_id, dst)
-- SENGAJA bebas — penulis sah pasca-posting hanya menyentuh itu (terukur 14 Sep: fulfill/recognize/void-reset/batch).
-- Predikat: journal_id INDUK (sales_invoices/bills/expenses) IS NOT NULL — sama dgn V244.

CREATE OR REPLACE FUNCTION law19_bekukan_baris()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    v_induk text := TG_ARGV[0];
    v_fk    text := TG_ARGV[1];
    v_jid   uuid;
    kol     text;
    i       int;
    v_lama  text;
    v_baru  text;
BEGIN
    EXECUTE format('SELECT journal_id FROM %I WHERE id = $1', v_induk)
        USING ((to_jsonb(NEW) ->> v_fk)::uuid) INTO v_jid;
    IF v_jid IS NULL THEN
        RETURN NEW;   -- induk belum dibukukan (draf) -> bebas
    END IF;
    FOR i IN 2 .. array_upper(TG_ARGV, 1) LOOP
        kol := TG_ARGV[i];
        IF NOT (to_jsonb(OLD) ? kol) THEN
            RAISE EXCEPTION 'Law 19 baris: kolom % tak ada di % (daftar pagar salah)', kol, TG_TABLE_NAME;
        END IF;
        v_lama := to_jsonb(OLD) ->> kol;
        v_baru := to_jsonb(NEW) ->> kol;
        IF v_lama::numeric IS DISTINCT FROM v_baru::numeric THEN
            RAISE EXCEPTION 'Law 19 baris: nominal %.% dokumen yang sudah dibukukan tak boleh diubah (id %, % -> %). Batalkan dokumen dan buat baru.',
                TG_TABLE_NAME, kol, OLD.id, v_lama, v_baru USING ERRCODE = 'check_violation';
        END IF;
    END LOOP;
    RETURN NEW;
END $$;

CREATE TRIGGER trg_law19_baris_beku BEFORE UPDATE ON sales_invoice_items
    FOR EACH ROW EXECUTE FUNCTION law19_bekukan_baris(
        'sales_invoices', 'invoice_id',
        'quantity', 'unit_price', 'discount_percent', 'discount_amount', 'tax_rate', 'tax_amount', 'subtotal', 'total', 'dpp');

CREATE TRIGGER trg_law19_baris_beku BEFORE UPDATE ON bill_items
    FOR EACH ROW EXECUTE FUNCTION law19_bekukan_baris(
        'bills', 'bill_id',
        'quantity', 'unit_price', 'discount_percent', 'discount_amount', 'tax_rate', 'tax_amount', 'subtotal', 'total', 'dpp');

CREATE TRIGGER trg_law19_baris_beku BEFORE UPDATE ON expense_items
    FOR EACH ROW EXECUTE FUNCTION law19_bekukan_baris('expenses', 'expense_id', 'amount');
