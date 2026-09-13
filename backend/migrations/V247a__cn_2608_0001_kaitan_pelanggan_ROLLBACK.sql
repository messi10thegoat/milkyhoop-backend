-- ROLLBACK V247a — tulis balik 'Toko Melati' ke CN-2608-0001, compare-and-set DICERMINKAN.
-- audit_logs hanya-tambah (trigger menolak UPDATE/DELETE): baris audit V247a TIDAK dihapus; rollback menambah
-- baris audit KOMPENSASI. Harus dijalankan SEBELUM rollback V247 dibalik urutannya (V247 dulu, lalu ini).

BEGIN;

DO $$
DECLARE
    v_cn       CONSTANT uuid := 'a389ccfb-5a1b-4aff-8ec7-a8901636194b';
    v_tenant   CONSTANT text := 'grapgrap-manado';
    v_lama     CONSTANT text := 'Toko Melati';
    v_sasaran  CONSTANT uuid := '15c07294-38cb-4523-8e11-1795b6c73069';
    v_cacah    integer;
    v_sebelum  record;
    v_sesudah  record;
BEGIN
    SELECT credit_note_number, status, total_amount, subtotal, tax_amount, discount_amount,
           amount_applied, amount_refunded, journal_id, original_invoice_id, customer_name
      INTO v_sebelum FROM credit_notes WHERE id = v_cn AND tenant_id = v_tenant;

    UPDATE credit_notes SET customer_id = v_lama
     WHERE id = v_cn AND tenant_id = v_tenant AND customer_id::text = v_sasaran::text;
    GET DIAGNOSTICS v_cacah = ROW_COUNT;
    IF v_cacah <> 1 THEN
        RAISE EXCEPTION 'ROLLBACK V247a: compare-and-set mengenai % baris, harus 1', v_cacah;
    END IF;

    SELECT credit_note_number, status, total_amount, subtotal, tax_amount, discount_amount,
           amount_applied, amount_refunded, journal_id, original_invoice_id, customer_name
      INTO v_sesudah FROM credit_notes WHERE id = v_cn AND tenant_id = v_tenant;
    IF v_sebelum IS DISTINCT FROM v_sesudah THEN
        RAISE EXCEPTION 'ROLLBACK V247a: kolom selain customer_id berubah: % -> %', v_sebelum, v_sesudah;
    END IF;

    INSERT INTO audit_logs (id, "eventType", entity_type, entity_id, entity_number, tenant_id, source, metadata, success, "createdAt")
    VALUES (gen_random_uuid()::text, 'DOCUMENT_DATA_CORRECTION_REVERTED', 'credit_note', v_cn, v_sebelum.credit_note_number,
            v_tenant, 'migration:V247a_ROLLBACK',
            jsonb_build_object('kolom', 'customer_id', 'nilai_lama', v_sasaran::text, 'nilai_baru', v_lama,
                               'alasan', 'rollback V247a'),
            true, now());
END $$;

COMMIT;
