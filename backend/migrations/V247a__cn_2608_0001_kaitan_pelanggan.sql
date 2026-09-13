-- V247a — perbaikan DATA: CN-2608-0001 (grapgrap-manado) customer_id 'Toko Melati' (NAMA) -> UUID pelanggan.
--
-- Putusan pemilik 13 Sep 2026: kaitkan ke pelanggan yang benar; nominal dan jurnal TIDAK disentuh.
-- Asal cacat: POST /api/credit-notes dulu menulis body.customer_id mentah tanpa validasi (ditutup 3661597c).
-- Harus diterapkan SEBELUM V247 (migrasi tipe varchar->uuid), dan terpisah darinya supaya bisa dibalik sendiri.
--
-- Semua syarat DITEGAKKAN di dalam migrasi, bukan diandaikan:
--   1 compare-and-set (nilai lama di WHERE)      2 tepat 1 baris terkena, selain itu RAISE
--   3 pelanggan sasaran ada tepat satu di tenant  4 nominal/jurnal/status/nomor identik sebelum & sesudah
--   6 jejak di audit_logs (hanya-tambah)

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
    -- 3. pelanggan sasaran ada, tepat satu, di tenant yang sama, dan namanya memang 'Toko Melati'
    SELECT count(*) INTO v_cacah FROM customers
     WHERE id = v_sasaran AND tenant_id = v_tenant AND lower(btrim(nama)) = lower(v_lama);
    IF v_cacah <> 1 THEN
        RAISE EXCEPTION 'V247a: pelanggan sasaran % (nama %) di % ditemukan % baris, harus 1', v_sasaran, v_lama, v_tenant, v_cacah;
    END IF;

    -- 4a. potret sebelum (nominal, jurnal, status, nomor)
    SELECT credit_note_number, status, total_amount, subtotal, tax_amount, discount_amount,
           amount_applied, amount_refunded, journal_id, original_invoice_id, customer_name
      INTO v_sebelum
      FROM credit_notes WHERE id = v_cn AND tenant_id = v_tenant;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'V247a: nota kredit % di % tak ditemukan', v_cn, v_tenant;
    END IF;

    -- 1+2. compare-and-set, tepat 1 baris
    UPDATE credit_notes SET customer_id = v_sasaran::text
     WHERE id = v_cn AND tenant_id = v_tenant AND customer_id = v_lama;
    GET DIAGNOSTICS v_cacah = ROW_COUNT;
    IF v_cacah <> 1 THEN
        RAISE EXCEPTION 'V247a: compare-and-set mengenai % baris, harus 1 (keadaan berubah? sudah diterapkan?)', v_cacah;
    END IF;

    -- 4b. potret sesudah: selain customer_id, SEMUA identik
    SELECT credit_note_number, status, total_amount, subtotal, tax_amount, discount_amount,
           amount_applied, amount_refunded, journal_id, original_invoice_id, customer_name
      INTO v_sesudah
      FROM credit_notes WHERE id = v_cn AND tenant_id = v_tenant;
    IF v_sebelum IS DISTINCT FROM v_sesudah THEN
        RAISE EXCEPTION 'V247a: kolom selain customer_id berubah: % -> %', v_sebelum, v_sesudah;
    END IF;

    -- 6. jejak audit (audit_logs hanya-tambah)
    INSERT INTO audit_logs (id, "eventType", entity_type, entity_id, entity_number, tenant_id, source, metadata, success, "createdAt")
    VALUES (gen_random_uuid()::text, 'DOCUMENT_DATA_CORRECTION', 'credit_note', v_cn, v_sebelum.credit_note_number,
            v_tenant, 'migration:V247a',
            jsonb_build_object(
                'kolom', 'customer_id',
                'nilai_lama', v_lama,
                'nilai_baru', v_sasaran::text,
                'alasan', 'putusan pemilik 13 Sep 2026: kaitan pelanggan salah format (nama tersimpan sebagai id), nominal tak disentuh',
                'nominal_tak_berubah', jsonb_build_object('total_amount', v_sebelum.total_amount, 'journal_id', v_sebelum.journal_id, 'status', v_sebelum.status)
            ),
            true, now());

    RAISE NOTICE 'V247a: % customer_id % -> % (1 baris; nominal, jurnal, status identik; audit tercatat)',
        v_sebelum.credit_note_number, v_lama, v_sasaran;
END $$;

COMMIT;
