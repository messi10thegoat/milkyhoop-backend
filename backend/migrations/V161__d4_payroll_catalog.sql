-- V161 — D4.1 catalog promotion (payroll roles)
-- =============================================================================
-- Scope: CATALOG ONLY. Tidak seed mapping (itu D4.2). Tidak refactor kode (itu D4.3).
--
-- TIER 1 promote (7) — MAPPED PENDING D4.2 seed:
--   SALARY_EXPENSE      — Beban Gaji (existing 5-20100 in seed_default_coa)
--   SALARY_PAYABLE      — Utang Gaji (existing 2-10400 in seed_default_coa)
--   PPH21_PAYABLE       — Utang PPh 21 (payroll-exclusive boundary, 2-10310)
--   BPJS_EE_PAYABLE     — Utang BPJS Karyawan (2-10410)
--   BPJS_ER_PAYABLE     — Utang BPJS Perusahaan (2-10420)
--   BPJS_ER_EXPENSE     — Beban BPJS Perusahaan (5-20150)
--   PPH21_ER_EXPENSE    — Beban PPh 21 Perusahaan / nett method (5-80100)
--
-- PPh 21 PAYROLL BOUNDARY (LOCKED §"PPH 21 PAYROLL BOUNDARY"):
--   - PPH21_PAYABLE -> 2-10310 (payroll-exclusive).
--   - JANGAN routing PPh 21 lewat WHT_PPH_PAYABLE (2-10320) yang khusus AP
--     transaksi (PPh 23/22/4(2)).
--
-- Idempotent: DROP + ADD CHECK constraint dengan whitelist lengkap (existing +
-- 7 baru). Ref: handover D4 recon (owner-approved 2026-06-05).
-- =============================================================================

BEGIN;

ALTER TABLE account_roles
    DROP CONSTRAINT IF EXISTS account_roles_role_key_check;

ALTER TABLE account_roles
    ADD CONSTRAINT account_roles_role_key_check CHECK (role_key = ANY (ARRAY[
        -- TIER 1
        'CASH_GENERAL',
        'BANK_OPERATIONAL',
        'AR_TRADE',
        'AR_OTHER',
        'INVENTORY_MERCHANDISE',
        'AP_TRADE',
        'CUSTOMER_DEPOSIT_LIABILITY',
        'EQUITY_OPENING_BALANCE',
        'REVENUE_SALES_GOODS',
        'REVENUE_SALES_RETURN',
        'COGS_SALES',
        'COGS_PURCHASE_RETURN',
        'REVENUE_DEFERRED',
        -- TIER 1 promoted (V155 Fase D1 — tax split)
        'VAT_OUTPUT',
        'VAT_INPUT',
        'WHT_PPH_PAYABLE',
        'WHT_PPH_PREPAID',
        -- TIER 1 promoted (V156 D2-wrap B)
        'AP_PREPAID',
        'PURCHASE_DISCOUNT',
        -- TIER 1 promoted (V157 D2-wrap D)
        'REVENUE_SALES_DISCOUNT',
        -- TIER 1 promoted (V158 D3.1 — manufaktur, MAPPED via V159 D3.2)
        'WIP_GENERIC',
        'COGS_VARIANCE_PRODUCTION',
        'WIP_SUBCONTRACT',
        'INVENTORY_ADJUSTMENT_EXPENSE',
        -- TIER 1 promoted (V161 D4.1 — payroll, MAPPED PENDING D4.2)
        'SALARY_EXPENSE',
        'SALARY_PAYABLE',
        'PPH21_PAYABLE',
        'BPJS_EE_PAYABLE',
        'BPJS_ER_PAYABLE',
        'BPJS_ER_EXPENSE',
        'PPH21_ER_EXPENSE',
        -- TIER 2
        'CASH_PETTY',
        -- TIER 3 (reserved, NOT seeded)
        'VAT_INPUT_NONCREDITABLE',
        'VAT_PAYABLE_NET',
        'WHT_PPH21',
        'WHT_PPH23',
        'WHT_PPH4_2',
        'WHT_PPH22',
        'ACCUMULATED_DEPRECIATION',
        'IC_SALES',
        'BRANCH_AR',
        'BRANCH_AP',
        -- FUTURE RESERVATION
        'AR_ALLOWANCE',
        'AP_ACCRUED',
        'INVENTORY_RAW',
        'INVENTORY_WIP',
        'INVENTORY_FINISHED',
        'INVENTORY_PACKAGING',
        'INVENTORY_WRITEOFF_DAMAGE',
        'INVENTORY_WRITEOFF_EXPIRED',
        'INVENTORY_WRITEOFF_SHRINKAGE',
        'INVENTORY_RECALL_LOSS',
        'MFG_DIRECT_LABOR',
        'MFG_OVERHEAD_INDIRECT_MATERIAL',
        'MFG_OVERHEAD_INDIRECT_LABOR',
        'MFG_OVERHEAD_UTILITIES',
        'MFG_OVERHEAD_DEPRECIATION',
        'MFG_OVERHEAD_APPLIED',
        'REVENUE_SALES_SERVICE',
        'REVENUE_UNBILLED',
        'COGS_PRODUCTION',
        'COGS_SERVICE',
        'COGS_VARIANCE_MATERIAL',
        'COGS_VARIANCE_LABOR',
        'COGS_VARIANCE_OVERHEAD',
        'CURRENCY_GAIN',
        'CURRENCY_LOSS',
        'CURRENCY_UNREALIZED_FX',
        -- V158 D3.1 reserved (forward-compat, NOT seeded)
        'WIP_RAW',
        'WIP_LABOR',
        'WIP_OVERHEAD',
        'FG_FINISHED',
        'WRITEOFF_DAMAGE',
        'WRITEOFF_EXPIRED',
        'WRITEOFF_SHRINKAGE'
    ]));

COMMIT;
