# FINDING: no user-facing path can set `"Tenant".is_pkp` (the flag that gates journal VAT)

**Date:** 2026-07-26
**Severity:** MEDIUM (correctness/onboarding gap; not a live data bug yet)
**Surfaced by:** FASE-4 step -1 provisioning of the non-PKP DP-flow tenant.

## What
`role_resolver.resolve_account_id_by_role_optional()` gates VAT line emission on
**`"Tenant".is_pkp`** (role_resolver.py:364-373): for `VAT_INPUT`/`VAT_OUTPUT`, if
`is_pkp` is false it returns `None` and the posting path skips the VAT line. That column
is the single source of truth for whether a tenant's journals carry PPN.

**`"Tenant".is_pkp` defaults to `TRUE` and is `NOT NULL`** (column default `true`). Every
tenant is born PKP. There is **no user-facing path to change it**:

| Path | Reality |
|---|---|
| `PATCH /api/settings/pkp` (pkp_settings.py) | writes **`tax_info.is_pkp`** — a *different* table, and that table **does not exist** in this DB (e-Faktur tables never built; recovery backlog). Endpoint would 500. |
| `PATCH /api/tenant/profile` (tenant_profile.py) | `UpdateTenantProfileRequest` allows only `display_name/address/phone/tax_id`. **`is_pkp` not accepted.** |
| onboarding_service | hardcodes the default (no PKP prompt in signup). |

Net: **a non-PKP tenant cannot be represented through any user path.** The one settings
endpoint that mentions PKP targets a table role_resolver never reads (and which is absent).

## Impact
- Any genuinely non-PKP UMKM (the majority of the target market) is silently PKP in the
  ledger-gating flag. If they enter a tax_code/rate, journals emit PPN they should not owe.
- Two divergent `is_pkp` columns (`"Tenant"` vs `tax_info`) with no reconciliation — a
  latent split-brain once `tax_info` is built.

## Harness workaround (documented, in step_-1_provision.sh)
Direct `UPDATE "Tenant" SET is_pkp=false` for the DP tenant. Justified because (a) it is a
config flag on a tenant we own, not financial data, and (b) no API exists. This is the only
`raw` write in the otherwise API-driven provisioning, and it is flagged as such.

## Recommended fix (backlog, owner-gated)
1. Add `is_pkp: Optional[bool]` to `UpdateTenantProfileRequest` (or a dedicated
   `PATCH /api/settings/pkp` that writes `"Tenant".is_pkp`, not the phantom `tax_info`), OR
2. Make `pkp_settings.py` write `"Tenant".is_pkp` and treat `tax_info` as the e-Faktur
   detail table it is (create it, or guard its absence). Reconcile the two columns.
3. Surface a PKP choice in onboarding so new tenants declare status at signup.
