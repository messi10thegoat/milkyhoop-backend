-- V258 (14 Sep 2026) — Hibah ADMIN penuh (CRUDVAPE) 8 modul master-data (putusan pemilik: Admin
-- "seperti pemilik" kecuali pengaturan kritis + hapus user). HANYA peran ADMIN; peran lain tak berubah.
-- Modul: BRANCH, COST_CENTER, PRICE_LIST, CURRENCY, RECIPE, KDS, PROFORMA, ITEM_BATCH.
-- actions & max_confidentiality DISALIN dari baris ADMIN yang sudah penuh (hindari tebak tipe enum/array).
-- ⚠️ cache role_permissions (kunci role_id) tak punya invalidasi → WAJIB restart gateway sesudah ini.

INSERT INTO role_permissions (id, role_id, module, actions, max_confidentiality, created_at)
SELECT gen_random_uuid(), r.id, m.module,
       (SELECT rp.actions FROM role_permissions rp
        WHERE rp.role_id = r.id AND array_length(rp.actions, 1) = 8 LIMIT 1),
       (SELECT rp.max_confidentiality FROM role_permissions rp WHERE rp.role_id = r.id LIMIT 1),
       NOW()
FROM roles r
CROSS JOIN (VALUES ('BRANCH'), ('COST_CENTER'), ('PRICE_LIST'), ('CURRENCY'),
                   ('RECIPE'), ('KDS'), ('PROFORMA'), ('ITEM_BATCH')) AS m(module)
WHERE r.code = 'ADMIN'
ON CONFLICT (role_id, module) DO UPDATE SET actions = EXCLUDED.actions;

-- ASSERT: ADMIN kini punya 8 modul dgn 8 aksi masing-masing.
DO $$
DECLARE v_n int;
BEGIN
    SELECT count(*) INTO v_n FROM role_permissions rp JOIN roles r ON r.id = rp.role_id
    WHERE r.code = 'ADMIN'
      AND rp.module IN ('BRANCH','COST_CENTER','PRICE_LIST','CURRENCY','RECIPE','KDS','PROFORMA','ITEM_BATCH')
      AND array_length(rp.actions, 1) = 8;
    IF v_n <> 8 THEN
        RAISE EXCEPTION 'V258: ADMIN hibah modul gagal (dapat % dari 8 penuh)', v_n;
    END IF;
    RAISE NOTICE 'V258 OK: ADMIN diberi 8 modul penuh.';
END $$;
