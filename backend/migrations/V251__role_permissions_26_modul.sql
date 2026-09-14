-- V251 (14 Sep 2026) — sweep izin tahap 2(b): baris role_permissions untuk 26 modul yang dipakai middleware tapi
-- TAK punya baris (akibatnya semua peran non-owner ditolak di rute terpetakan modul itu). Putusan pemilik lewat MASTER:
-- tiap modul MENIRU aksi per-peran dari SATU modul analog yang sudah berbaris, lalu 6 koreksi praktik akuntansi/SoD.
-- OWNER tak terpengaruh (can() bypass). Cache role_permissions tanpa invalidasi -> WAJIB restart gateway sesudah migrasi.

DO $$
DECLARE
    v_modul text;
    v_ada int;
    v_baru text[] := ARRAY['EXPENSE','DEBIT_NOTE','QUOTE','SALES_ORDER','CREDIT_NOTE','TABLES','CUSTOMER_DEPOSIT',
        'VENDOR_DEPOSIT','STOCK_ADJUST','WAREHOUSE','UNIT','BOM','WORK_ORDER','WORK_CENTER','MATERIAL_ISSUE','FG_RECEIPT',
        'FIXED_ASSET','INTERCOMPANY','LEDGER','PERIOD','BUDGET','AR_AGING','EMPLOYEE','BPJS','PAY_GROUP','SALARY_COMPONENT'];
BEGIN
    FOREACH v_modul IN ARRAY v_baru LOOP
        SELECT count(*) INTO v_ada FROM role_permissions WHERE module = v_modul;
        IF v_ada <> 0 THEN
            RAISE EXCEPTION 'V251: modul % sudah punya % baris (diharapkan 0) — pengukuran 14 Sep berubah, batalkan', v_modul, v_ada;
        END IF;
    END LOOP;
END $$;

-- 1) SALIN dari analog (SEMUA peran termasuk OWNER; OWNER bypass tetap, disalin agar tabel seragam)
INSERT INTO role_permissions (id, role_id, module, actions, max_confidentiality, created_at)
SELECT gen_random_uuid(), rp.role_id, m.modul_baru, rp.actions, rp.max_confidentiality, now()
FROM (VALUES
    ('EXPENSE','BILL'),('DEBIT_NOTE','BILL'),
    ('QUOTE','INVOICE'),('SALES_ORDER','INVOICE'),('CREDIT_NOTE','INVOICE'),('TABLES','INVOICE'),
    ('CUSTOMER_DEPOSIT','RECEIPT'),('VENDOR_DEPOSIT','PAYMENT'),
    ('STOCK_ADJUST','PRODUCT'),('WAREHOUSE','PRODUCT'),('UNIT','PRODUCT'),('BOM','PRODUCT'),
    ('WORK_ORDER','PRODUCT'),('WORK_CENTER','PRODUCT'),('MATERIAL_ISSUE','PRODUCT'),('FG_RECEIPT','PRODUCT'),
    ('FIXED_ASSET','JOURNAL'),('INTERCOMPANY','JOURNAL'),('LEDGER','JOURNAL'),('PERIOD','JOURNAL'),
    ('BUDGET','REPORT'),('AR_AGING','REPORT'),
    ('EMPLOYEE','PAYROLL'),('BPJS','PAYROLL'),('PAY_GROUP','PAYROLL'),('SALARY_COMPONENT','PAYROLL')
) AS m(modul_baru, analog)
JOIN role_permissions rp ON rp.module = m.analog;

-- ============ 6 KOREKSI (dideklarasi; gerbang meng-assert TEPAT ini yang beda dari analog) ============

-- Koreksi 1: kas kecil. CASHIER & STORE_STAFF pada EXPENSE = C,R saja. BILL tak punya baris keduanya -> INSERT.
INSERT INTO role_permissions (id, role_id, module, actions, max_confidentiality, created_at)
SELECT gen_random_uuid(), r.id, 'EXPENSE', ARRAY['C','R'], NULL::confidentiality_level, now()
FROM roles r WHERE r.code IN ('CASHIER','STORE_STAFF')
  AND NOT EXISTS (SELECT 1 FROM role_permissions rp WHERE rp.role_id=r.id AND rp.module='EXPENSE');

-- Koreksi 2: PERIOD C/U/P/V hanya ADMIN & FINANCE_MGR; peran lain (ACCOUNTANT, BENDAHARA, HR_PAYROLL, COLLABORATOR) -> R.
UPDATE role_permissions SET actions = ARRAY['R']
WHERE module='PERIOD' AND role_id IN (SELECT id FROM roles WHERE code NOT IN ('ADMIN','FINANCE_MGR','OWNER'));

-- Koreksi 3: VIEWER R pada FIXED_ASSET, LEDGER, PERIOD, INTERCOMPANY (JOURNAL tak punya baris VIEWER) -> INSERT.
INSERT INTO role_permissions (id, role_id, module, actions, max_confidentiality, created_at)
SELECT gen_random_uuid(), r.id, m.modul, ARRAY['R'], NULL::confidentiality_level, now()
FROM roles r CROSS JOIN (VALUES ('FIXED_ASSET'),('LEDGER'),('PERIOD'),('INTERCOMPANY')) AS m(modul)
WHERE r.code='VIEWER' AND NOT EXISTS (SELECT 1 FROM role_permissions rp WHERE rp.role_id=r.id AND rp.module=m.modul);

-- Koreksi 4: BUDGET ACCOUNTANT C,R,U; FINANCE_MGR C,R,U,A (ADMIN tetap tiruan CRUDVAPE).
UPDATE role_permissions SET actions = ARRAY['C','R','U']
WHERE module='BUDGET' AND role_id = (SELECT id FROM roles WHERE code='ACCOUNTANT');
UPDATE role_permissions SET actions = ARRAY['C','R','U','A']
WHERE module='BUDGET' AND role_id = (SELECT id FROM roles WHERE code='FINANCE_MGR');

-- Koreksi 5: EMPLOYEE/BPJS/PAY_GROUP/SALARY_COMPONENT hanya HR_PAYROLL & ADMIN (kerahasiaan gaji) -> buang COLLABORATOR.
DELETE FROM role_permissions
WHERE module IN ('EMPLOYEE','BPJS','PAY_GROUP','SALARY_COMPONENT')
  AND role_id IN (SELECT id FROM roles WHERE code NOT IN ('HR_PAYROLL','ADMIN','OWNER'));

-- Koreksi 6: CREDIT_NOTE — SALES & STORE_STAFF tanpa P dan tanpa V (posting nota kredit oleh ACCT/FINANCE/BENDAHARA/ADMIN).
UPDATE role_permissions SET actions = array_remove(array_remove(actions,'P'),'V')
WHERE module='CREDIT_NOTE' AND role_id IN (SELECT id FROM roles WHERE code IN ('SALES','STORE_STAFF'));
