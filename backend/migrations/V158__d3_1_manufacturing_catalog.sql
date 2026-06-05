-- V158 — D3.1 catalog promotion (manufacturing + writeoff forward-compat)
-- =============================================================================
-- Scope: CATALOG ONLY. Tidak seed mapping ke account_roles (itu D3.2).
--        Tidak refactor kode posting (itu D3.3).
--
-- TIER 1 promote (4) — MAPPED PENDING D3.2 seed:
--   WIP_GENERIC                    — single WIP bucket (semua produksi unified)
--   COGS_VARIANCE_PRODUCTION       — varian total (material+labor+overhead lumped)
--   WIP_SUBCONTRACT                — biaya subkontrak/maklon
--   INVENTORY_ADJUSTMENT_EXPENSE   — generic stock adjustment loss
--
-- Reserved (8) — forward-compat, BELUM TIER 1, BELUM seed:
--   WIP_RAW, WIP_LABOR, WIP_OVERHEAD  — granular manufaktur tier (future split)
--   FG_FINISHED                       — keputusan D3.1: FG TIDAK dipisah sekarang,
--                                       semua FG -> INVENTORY_MERCHANDISE.
--   WRITEOFF_DAMAGE, WRITEOFF_EXPIRED, WRITEOFF_SHRINKAGE
--                                     — forward-compat farmasi/F&B
--                                       (existing INVENTORY_WRITEOFF_* tetap di catalog,
--                                        ini alias singkat untuk future split).
--
-- Idempotent: DROP + ADD CHECK constraint dengan whitelist baru.
-- Ref: recon Step 1 (owner-approved 2026-06-05), handover
--      DOCS/plans/2026-06-05-coa-role-migration-handover.md §5.
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
        -- TIER 1 promoted (V158 D3.1 — manufaktur, MAPPED PENDING D3.2)
        'WIP_GENERIC',
        'COGS_VARIANCE_PRODUCTION',
        'WIP_SUBCONTRACT',
        'INVENTORY_ADJUSTMENT_EXPENSE',
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
