-- V250 (14 Sep 2026) — pagar DB credit_notes.original_invoice_id (Law 19 / Law 13-pola "invariant ditegakkan di DB").
-- original_invoice_id = atribusi piutang yang dibaca compute_ar_outstanding cabang 2. Sesudah CN keluar dari draf, kaitan
-- hanya boleh berubah BERPASANGAN dengan riwayat penerapan di transaksi yang sama:
--   NULL -> X : wajib ada aplikasi AKTIF (cn, X)                       [apply unit B]
--   X -> NULL : wajib TAK ada aplikasi aktif (cn, X) DAN ada aplikasi (cn, X) yang dibatalkan DI TRANSAKSI INI
--               (reversed_at = now(), waktu mulai transaksi)           [unapply unit (2)]
--   X -> Y    : selalu ditolak (tunjuk-ulang = unapply lalu apply)
-- Draf bebas (pembuat/penyunting draf; divalidasi handler). Penulis sah terukur: create (INSERT), PATCH draf, apply, unapply.
-- Handler apply/unapply diurutkan ulang (aplikasi dulu, kaitan kemudian) di commit yang sama.

CREATE OR REPLACE FUNCTION credit_note_kaitan_faktur_beku()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF OLD.status = 'draft' THEN
        RETURN NEW;
    END IF;

    IF OLD.original_invoice_id IS NOT NULL AND NEW.original_invoice_id IS NOT NULL THEN
        RAISE EXCEPTION 'Law 19: kaitan faktur nota kredit % sudah terisi (%) dan tak boleh ditunjuk-ulang ke %; batalkan penerapan dulu.',
            OLD.credit_note_number, OLD.original_invoice_id, NEW.original_invoice_id
            USING ERRCODE = 'check_violation';
    END IF;

    IF OLD.original_invoice_id IS NULL THEN
        IF NOT EXISTS (SELECT 1 FROM credit_note_applications a
                       WHERE a.credit_note_id = NEW.id AND a.invoice_id = NEW.original_invoice_id
                         AND a.tenant_id = NEW.tenant_id AND a.status = 'active') THEN
            RAISE EXCEPTION 'Law 19: kaitan faktur nota kredit % hanya boleh diisi lewat penerapan (aplikasi aktif ke faktur %).',
                OLD.credit_note_number, NEW.original_invoice_id
                USING ERRCODE = 'check_violation';
        END IF;
        RETURN NEW;
    END IF;

    -- X -> NULL
    IF EXISTS (SELECT 1 FROM credit_note_applications a
               WHERE a.credit_note_id = OLD.id AND a.invoice_id = OLD.original_invoice_id AND a.status = 'active')
       OR NOT EXISTS (SELECT 1 FROM credit_note_applications a
                      WHERE a.credit_note_id = OLD.id AND a.invoice_id = OLD.original_invoice_id
                        AND a.status = 'reversed' AND a.reversed_at = now()) THEN
        RAISE EXCEPTION 'Law 19: kaitan faktur nota kredit % hanya boleh dilepas lewat pembatalan penerapan di transaksi yang sama.',
            OLD.credit_note_number
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END $$;

CREATE TRIGGER trg_credit_note_kaitan_faktur_beku
    BEFORE UPDATE OF original_invoice_id ON credit_notes
    FOR EACH ROW
    WHEN (OLD.original_invoice_id IS DISTINCT FROM NEW.original_invoice_id)
    EXECUTE FUNCTION credit_note_kaitan_faktur_beku();
