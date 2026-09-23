-- V296: "Tenant".is_pkp DEFAULT true -> false. Forward-only; existing rows are NOT touched.
-- V154 added the column NOT NULL DEFAULT true "for backward compatibility", and onboarding
-- (services/onboarding_service.create_tenant_and_user) INSERTs "Tenant" without is_pkp, so EVERY
-- tenant was born PKP (allowed to collect PPN) -- while all schema.prisma files say
-- @default(false) and mask it. Being PKP must be DECLARED, never defaulted.
-- Existing tenants are corrected one at a time through PATCH /api/settings/pkp-status after the
-- owner confirms each one's real status (grapgrap = non-PKP confirmed 23 Sep 2026).
ALTER TABLE "Tenant" ALTER COLUMN is_pkp SET DEFAULT false;

COMMENT ON COLUMN "Tenant".is_pkp IS
  'Pengusaha Kena Pajak: boleh memungut PPN. DEFAULT false (V296): dinyatakan pemilik lewat PATCH /api/settings/pkp-status, tak pernah default.';
