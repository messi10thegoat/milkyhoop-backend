-- V230: jejak audit PENGHAPUSAN DOKUMEN — fungsi + 7 trigger. NOL tabel baru.
--
-- LATAR: forensik 2026-09-03 atas dugaan hilangnya dua faktur owner tidak bisa
-- menjawab "siapa dan kapan", karena sistem ini TIDAK punya jejak audit
-- penghapusan dokumen sama sekali. `audit_logs` hari itu hanya memuat LOGIN
-- (48) dan FAILED_LOGIN (1); pencarian nomor faktur yang hilang di SELURUH
-- tabel mengembalikan 0 baris. Tambalan ini menutup lubang itu.
--
-- KENAPA `audit_logs`, BUKAN TABEL BARU: tabel itu SUDAH append-only dan
-- ditegakkan database — tiga trigger AKTIF (`trg_audit_immutable`,
-- `trg_prevent_audit_log_delete`, `trg_prevent_audit_log_update`) yang
-- me-RAISE EXCEPTION pada setiap UPDATE dan DELETE. Itu persis sifat yang
-- dituntut jejak audit, sudah ada dan sudah terbukti jalan. Tabel baru harus
-- dibangun perlindungannya dari nol, dan perlindungan yang belum teruji lebih
-- lemah daripada yang sudah berjalan.
-- Bentuknya pun muat tanpa DDL: semua kolom `audit_logs` nullable kecuali `id`
-- (punya default), nol CHECK constraint, dan `entity_id` bertipe uuid sama
-- seperti kolom `id` ketujuh tabel dokumen.
--
-- ⚠️ RETENSI: `audit_retention_policies` ADA (kini 0 baris, jadi nol
-- pemangkasan). Bila fitur retensi kelak diaktifkan,
-- **eventType = 'DOCUMENT_DELETED' WAJIB DIKECUALIKAN dari pemangkasan** —
-- kalau tidak, justru bukti penghapusan dokumen yang ikut terhapus. Logikanya
-- SENGAJA belum dibangun di sini; catatan yang sama ada di `routers/audit.py`.
--
-- ⚠️ RLS: `audit_logs` TIDAK ber-RLS (ketujuh tabel dokumen ber-RLS). Itu
-- DISENGAJA tidak diubah: menyalakan RLS pada tabel bersama yang sedang
-- dipakai jalur auth punya blast radius di luar tiket ini, dan menurut Law 24
-- RLS bersifat dekoratif untuk lalu lintas gateway yang BYPASSRLS. Dicatat
-- sebagai tiket terpisah, bukan didiamkan.
--
-- ⚠️ AUDIT, BUKAN GUARD: fungsi ini TIDAK BOLEH memblokir penghapusan. Semua
-- galat ditangkap; kegagalan menulis audit dilaporkan lewat RAISE WARNING
-- (dengan SQLERRM) supaya terlihat di log Postgres, TIDAK hening — lalu
-- penghapusan tetap dilanjutkan.
--
-- Nol sentuhan pada trigger Iron Law mana pun. Nol DISABLE TRIGGER.

CREATE OR REPLACE FUNCTION log_document_deletion()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    j jsonb;
BEGIN
    BEGIN
        j := to_jsonb(OLD);

        INSERT INTO audit_logs (
            "eventType", entity_type, entity_id, entity_number,
            tenant_id, "userId", source, success, input_data, metadata
        ) VALUES (
            'DOCUMENT_DELETED',
            TG_TABLE_NAME,
            (j->>'id')::uuid,
            -- Nama kolom nomor berbeda per tabel; dipungut dari snapshot
            -- supaya tetap SATU fungsi, bukan tujuh.
            COALESCE(
                j->>'invoice_number',   -- sales_invoices, bills
                j->>'order_number',     -- sales_orders
                j->>'quote_number',     -- quotes
                j->>'proforma_number',  -- proformas
                j->>'deposit_number',   -- customer_deposits
                j->>'po_number'         -- purchase_orders
            ),
            j->>'tenant_id',
            -- NULL hari ini kecuali endpoint penghapus men-set GUC ini DI
            -- DALAM transaksi. `SET LOCAL` di luar blok transaksi TIDAK
            -- berefek (terukur: WARNING "SET LOCAL can only be used in
            -- transaction blocks", statement berikutnya membaca kosong).
            NULLIF(current_setting('app.user_id', true), ''),
            'db_trigger',
            true,
            j,  -- snapshot BARIS PENUH; satu-satunya jejak yang tersisa
            jsonb_build_object(
                'status_saat_hapus', j->>'status',
                'total_amount', COALESCE(j->>'total_amount', j->>'amount')
            )
        );
    EXCEPTION WHEN OTHERS THEN
        RAISE WARNING
            '[V230] gagal mencatat penghapusan %.% : % (%)',
            TG_TABLE_NAME, COALESCE(j->>'id', '?'), SQLERRM, SQLSTATE;
    END;

    RETURN OLD;  -- selalu; audit tak pernah membatalkan penghapusan
END;
$$;

COMMENT ON FUNCTION log_document_deletion() IS
    'V230: mencatat penghapusan dokumen ke audit_logs (eventType=DOCUMENT_DELETED). Tak pernah memblokir DELETE.';

-- `proformas` TIDAK punya jalur DELETE di router mana pun hari ini (hanya
-- cancel), jadi trigger-nya tak akan menyala. Tetap dipasang: biayanya nol dan
-- ia menutup jalur yang mungkin ditambahkan besok.
DROP TRIGGER IF EXISTS trg_log_deletion ON sales_invoices;
CREATE TRIGGER trg_log_deletion BEFORE DELETE ON sales_invoices
    FOR EACH ROW EXECUTE FUNCTION log_document_deletion();

DROP TRIGGER IF EXISTS trg_log_deletion ON sales_orders;
CREATE TRIGGER trg_log_deletion BEFORE DELETE ON sales_orders
    FOR EACH ROW EXECUTE FUNCTION log_document_deletion();

DROP TRIGGER IF EXISTS trg_log_deletion ON quotes;
CREATE TRIGGER trg_log_deletion BEFORE DELETE ON quotes
    FOR EACH ROW EXECUTE FUNCTION log_document_deletion();

DROP TRIGGER IF EXISTS trg_log_deletion ON bills;
CREATE TRIGGER trg_log_deletion BEFORE DELETE ON bills
    FOR EACH ROW EXECUTE FUNCTION log_document_deletion();

DROP TRIGGER IF EXISTS trg_log_deletion ON proformas;
CREATE TRIGGER trg_log_deletion BEFORE DELETE ON proformas
    FOR EACH ROW EXECUTE FUNCTION log_document_deletion();

DROP TRIGGER IF EXISTS trg_log_deletion ON customer_deposits;
CREATE TRIGGER trg_log_deletion BEFORE DELETE ON customer_deposits
    FOR EACH ROW EXECUTE FUNCTION log_document_deletion();

DROP TRIGGER IF EXISTS trg_log_deletion ON purchase_orders;
CREATE TRIGGER trg_log_deletion BEFORE DELETE ON purchase_orders
    FOR EACH ROW EXECUTE FUNCTION log_document_deletion();
