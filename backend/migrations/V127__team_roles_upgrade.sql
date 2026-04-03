-- V127: Team Roles Upgrade
-- Adds 6 new system roles: ADMIN, BENDAHARA, PRODUCTION_MGR, STORE_STAFF, TAX_OFFICER, COLLABORATOR
-- Adds TAX permission module for existing roles
-- Adds is_external column to user_tenant_roles

BEGIN;

-- =============================================================================
-- 1. INSERT 6 NEW ROLES
-- =============================================================================

INSERT INTO roles (id, tenant_id, code, name, description, hierarchy_level, is_system, is_active, approval_limit, permissions, created_at)
VALUES
  (gen_random_uuid(), '__SYSTEM__', 'ADMIN', 'Admin', 'Akses penuh kecuali pengaturan kritis dan penghapusan user', 0, true, true, NULL, '["*","!settings:delete","!user_management:delete"]', now()),
  (gen_random_uuid(), '__SYSTEM__', 'BENDAHARA', 'Bendahara', 'Mengelola keuangan, rekonsiliasi bank, dan pengeluaran', 1, true, true, 50000000, '[]', now()),
  (gen_random_uuid(), '__SYSTEM__', 'PRODUCTION_MGR', 'Manajer Produksi', 'Pembelian bahan baku, manajemen inventory, dan pelaporan produksi', 1, true, true, 0, '[]', now()),
  (gen_random_uuid(), '__SYSTEM__', 'STORE_STAFF', 'Staf Toko', 'Transaksi penjualan, kas kecil, dan stok barang jadi', 2, true, true, 0, '[]', now()),
  (gen_random_uuid(), '__SYSTEM__', 'TAX_OFFICER', 'Tax Officer', 'e-Faktur, Coretax, laporan pajak, dan kepatuhan perpajakan', 1, true, true, 0, '[]', now()),
  (gen_random_uuid(), '__SYSTEM__', 'COLLABORATOR', 'Collaborator', 'Akses read-only untuk pihak eksternal (akuntan, auditor)', 2, true, true, 0, '[]', now())
ON CONFLICT DO NOTHING;

-- =============================================================================
-- 2. INSERT ROLE_PERMISSIONS FOR NEW ROLES
-- =============================================================================

-- ADMIN: all 15 modules full access except USER_MANAGEMENT (no D) and SETTINGS (R,U only)
INSERT INTO role_permissions (id, role_id, module, actions)
SELECT gen_random_uuid(), r.id, m.module, m.actions::char[]
FROM roles r
CROSS JOIN (VALUES
  ('ACCOUNT',         '{C,R,U,D,V,A,P,E}'),
  ('BANK',            '{C,R,U,D,V,A,P,E}'),
  ('BILL',            '{C,R,U,D,V,A,P,E}'),
  ('CUSTOMER',        '{C,R,U,D,V,A,P,E}'),
  ('INVOICE',         '{C,R,U,D,V,A,P,E}'),
  ('JOURNAL',         '{C,R,U,D,V,A,P,E}'),
  ('PAYMENT',         '{C,R,U,D,V,A,P,E}'),
  ('PAYROLL',         '{C,R,U,D,V,A,P,E}'),
  ('PRODUCT',         '{C,R,U,D,V,A,P,E}'),
  ('RECEIPT',         '{C,R,U,D,V,A,P,E}'),
  ('REPORT',          '{C,R,U,D,V,A,P,E}'),
  ('TAX',             '{C,R,U,D,V,A,P,E}'),
  ('VENDOR',          '{C,R,U,D,V,A,P,E}'),
  ('USER_MANAGEMENT', '{C,R,U,A,P}'),
  ('SETTINGS',        '{R,U}')
) AS m(module, actions)
WHERE r.code = 'ADMIN' AND r.tenant_id = '__SYSTEM__';

-- BENDAHARA: finance + bank + reporting, read customer/vendor, read tax
INSERT INTO role_permissions (id, role_id, module, actions)
SELECT gen_random_uuid(), r.id, m.module, m.actions::char[]
FROM roles r
CROSS JOIN (VALUES
  ('ACCOUNT',  '{R,U,P,E}'),
  ('BANK',     '{C,R,U,D,P,E}'),
  ('BILL',     '{C,R,U,V,A,P,E}'),
  ('INVOICE',  '{C,R,U,V,A,P,E}'),
  ('JOURNAL',  '{C,R,U,V,P,E}'),
  ('PAYMENT',  '{C,R,U,D,V,A,P,E}'),
  ('RECEIPT',  '{C,R,U,D,V,A,P,E}'),
  ('REPORT',   '{R,P,E}'),
  ('CUSTOMER', '{R,P}'),
  ('VENDOR',   '{R,P}'),
  ('SETTINGS', '{R}'),
  ('TAX',      '{R,P}')
) AS m(module, actions)
WHERE r.code = 'BENDAHARA' AND r.tenant_id = '__SYSTEM__';

-- PRODUCTION_MGR: purchasing + inventory, read invoice/report
INSERT INTO role_permissions (id, role_id, module, actions)
SELECT gen_random_uuid(), r.id, m.module, m.actions::char[]
FROM roles r
CROSS JOIN (VALUES
  ('BILL',     '{C,R,U,P,E}'),
  ('PAYMENT',  '{C,R,U,P}'),
  ('PRODUCT',  '{C,R,U,D,P}'),
  ('VENDOR',   '{C,R,U,P}'),
  ('REPORT',   '{R,P}'),
  ('INVOICE',  '{R}'),
  ('CUSTOMER', '{R}')
) AS m(module, actions)
WHERE r.code = 'PRODUCTION_MGR' AND r.tenant_id = '__SYSTEM__';

-- STORE_STAFF: POS + petty cash
INSERT INTO role_permissions (id, role_id, module, actions)
SELECT gen_random_uuid(), r.id, m.module, m.actions::char[]
FROM roles r
CROSS JOIN (VALUES
  ('INVOICE',  '{C,R,U,P}'),
  ('RECEIPT',  '{C,R,U,P}'),
  ('PRODUCT',  '{R,P}'),
  ('CUSTOMER', '{C,R,U,P}'),
  ('PAYMENT',  '{C,R,P}'),
  ('REPORT',   '{R}')
) AS m(module, actions)
WHERE r.code = 'STORE_STAFF' AND r.tenant_id = '__SYSTEM__';

-- TAX_OFFICER: tax + read financial docs
INSERT INTO role_permissions (id, role_id, module, actions)
SELECT gen_random_uuid(), r.id, m.module, m.actions::char[]
FROM roles r
CROSS JOIN (VALUES
  ('TAX',      '{C,R,U,D,P,E}'),
  ('REPORT',   '{R,P,E}'),
  ('INVOICE',  '{R,P}'),
  ('BILL',     '{R,P}'),
  ('CUSTOMER', '{R}'),
  ('VENDOR',   '{R}'),
  ('SETTINGS', '{R}')
) AS m(module, actions)
WHERE r.code = 'TAX_OFFICER' AND r.tenant_id = '__SYSTEM__';

-- COLLABORATOR: read-only all, export reports
INSERT INTO role_permissions (id, role_id, module, actions)
SELECT gen_random_uuid(), r.id, m.module, m.actions::char[]
FROM roles r
CROSS JOIN (VALUES
  ('ACCOUNT',         '{R}'),
  ('BANK',            '{R}'),
  ('BILL',            '{R}'),
  ('CUSTOMER',        '{R}'),
  ('INVOICE',         '{R}'),
  ('JOURNAL',         '{R}'),
  ('PAYMENT',         '{R}'),
  ('PAYROLL',         '{R}'),
  ('PRODUCT',         '{R}'),
  ('RECEIPT',         '{R}'),
  ('REPORT',          '{R,P,E}'),
  ('TAX',             '{R}'),
  ('VENDOR',          '{R}'),
  ('USER_MANAGEMENT', '{R}'),
  ('SETTINGS',        '{R}')
) AS m(module, actions)
WHERE r.code = 'COLLABORATOR' AND r.tenant_id = '__SYSTEM__';

-- =============================================================================
-- 3. ADD TAX PERMISSIONS FOR EXISTING ROLES
-- =============================================================================

INSERT INTO role_permissions (id, role_id, module, actions)
SELECT gen_random_uuid(), r.id, 'TAX', m.actions::char[]
FROM roles r
CROSS JOIN (VALUES
  ('OWNER',       '{C,R,U,D,V,A,P,E}'),
  ('FINANCE_MGR', '{C,R,U,D,P,E}'),
  ('ACCOUNTANT',  '{R,P,E}')
) AS m(code, actions)
WHERE r.code = m.code AND r.tenant_id = '__SYSTEM__'
ON CONFLICT DO NOTHING;

-- =============================================================================
-- 4. INSERT ROLE_VISIBILITY FOR NEW ROLES
-- =============================================================================

INSERT INTO role_visibility (id, role_id, level)
SELECT gen_random_uuid(), r.id, v.level::confidentiality_level
FROM roles r
CROSS JOIN (VALUES
  ('ADMIN',          'L5'),
  ('BENDAHARA',      'L3'),
  ('PRODUCTION_MGR', 'L2'),
  ('STORE_STAFF',    'L1'),
  ('TAX_OFFICER',    'L2'),
  ('COLLABORATOR',   'L1')
) AS v(code, level)
WHERE r.code = v.code AND r.tenant_id = '__SYSTEM__';

-- =============================================================================
-- 5. ADD is_external COLUMN TO user_tenant_roles
-- =============================================================================

ALTER TABLE user_tenant_roles ADD COLUMN IF NOT EXISTS is_external BOOLEAN NOT NULL DEFAULT FALSE;

COMMENT ON COLUMN user_tenant_roles.is_external IS 'True for external collaborators (accountants, auditors). Auto-set when role=COLLABORATOR.';

COMMIT;
