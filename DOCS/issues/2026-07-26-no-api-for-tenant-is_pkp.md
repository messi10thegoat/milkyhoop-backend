# PRODUCT BLOCKER (owner decision): every tenant is born PKP and cannot leave

**Date:** 2026-07-26
**Type:** PRODUCT BLOCKER (not a technical ticket) — target-market fit + tax compliance.
**Surfaced by:** FASE-4 DP-flow provisioning of a non-PKP tenant.

## The blocker, in one line
`"Tenant".is_pkp` **defaults TRUE, is NOT NULL, and has zero working paths to change it.**
Result: **every tenant is created PKP and can never become non-PKP** through the product.

## Market context (why this is a product decision, not a bug note)
The majority of Indonesian UMKM sit **below the 4,8 miliar/year PKP threshold → they are
NON-PKP.** A product that targets UMKM currently forces PKP status on the segment that is
mostly *not* PKP. The only mechanism that actually flips the ledger-gating flag today is a
**raw `UPDATE "Tenant" SET is_pkp=false`** in the DB — no user, and no support agent through
the UI, can do it.

## Why no path works
| Path | Reality |
|---|---|
| `PATCH /api/settings/pkp` (pkp_settings.py) | writes **`tax_info.is_pkp`** — a different table, and **`tax_info` does not exist in the DB** (see below). Would 500. |
| `PATCH /api/tenant/profile` (tenant_profile.py) | `UpdateTenantProfileRequest` accepts only `display_name/address/phone/tax_id`. `is_pkp` not allowed. |
| onboarding_service | hardcodes the default; no PKP prompt at signup. |
| DB column | `is_pkp boolean NOT NULL DEFAULT true`. |

## Scope of impact — what `"Tenant".is_pkp` actually gates (grep of ALL readers)
`"Tenant".is_pkp` is read by **exactly ONE site**:
- `role_resolver.py:365` — `resolve_account_id_by_role_optional()` for **VAT_INPUT / VAT_OUTPUT**.
  If false → returns None → the posting path emits **no VAT (PPN) line**.

So the *direct* ledger impact is bounded: **VAT line emission on documents that carry tax.**
(Every other `is_pkp` in the codebase is a *different* field: `customers.is_pkp`,
`vendors.is_pkp` = the counterparty's own status; `tax_info.is_pkp` = read by efaktur.py /
tax_invoices.py, a table that isn't built.) Bounded, but decisive: a forced-PKP tenant that
enters any tax_code/rate will book PPN it may not owe, and its financial statements carry a
tax posture the business never chose.

## Compounding finding: tax identity + e-Faktur never built
`tax_info` appears in DATABASE_SCHEMA_REFERENCE but **does not exist in the live DB.** That
table holds NPWP/PKP identity, and `efaktur.py` / `tax_invoices.py` depend on it. So:
- the PKP *settings* screen writes to a table that isn't there, and
- **tax identity (NPWP) + e-Faktur are effectively not implemented.**
For a product whose differentiator is Indonesian tax compliance, this is material.

## What the owner must decide
1. Should new tenants **declare PKP status at signup** (default non-PKP for UMKM)?
2. Do we ship a **working** way to change it: add `is_pkp` to tenant-profile update, OR
   repoint `pkp_settings.py` at `"Tenant".is_pkp` and build/guard `tax_info`?
3. Is **e-Faktur / NPWP** in-scope now (build `tax_info` + identity), or explicitly deferred?

## Harness handling (interim, documented)
`step_-1_provision.sh` does the raw `UPDATE "Tenant" SET is_pkp=false` for the DP tenant,
clearly marked as an API-gap workaround. It is the only raw write in otherwise API-driven
provisioning, and exists solely because no product path can express "non-PKP".
