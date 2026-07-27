# BUG: no API path to provision a loggable team member (invite 400 + orphaned accept flow)

**Date:** 2026-07-27
**Severity:** HIGH (team onboarding is non-functional via API for fresh tenants) + blocks
validation of permission dimension D.
**Status:** RUNTIME-CONFIRMED (HTTP 400) + code-confirmed (no team_invitations writer).

## What (two compounding defects)

### Defect 1 — invite 400 "Invalid role_id" for every fresh tenant (HTTP-confirmed)
`POST /api/team-members/invite` with a valid VIEWER role_id returns **400 "Invalid role_id"**.
Root cause: the handler (team_members.py:390) looks up the role tenant-scoped —
`SELECT ... FROM roles WHERE id = $1 AND tenant_id = $2` with `$2` = the acting tenant — but ALL
roles live under the GLOBAL sentinel `tenant_id = '__SYSTEM__'` (14 roles: OWNER, ADMIN, VIEWER,
CASHIER, STORE_STAFF, ...). Onboarding assigns the tenant its OWNER via a `__SYSTEM__` role and
seeds NO per-tenant `roles` rows, so the tenant-scoped lookup can never match. Every fresh tenant
therefore cannot invite anyone. (Verified: `roles` has 0 rows for the tenant, 14 under
`__SYSTEM__`; the owner's `user_tenant_roles.role_id` points at the `__SYSTEM__` OWNER.)

### Defect 2 — even if invite worked, the invited user can never log in
- `team-members/invite` inserts `"User"` + `user_tenant_roles` directly but sets **no
  `passwordHash`** and creates **no invitation token**.
- The only credential-setting path, `POST /api/invite/{token}/accept` (invite_public.py:174),
  reads `team_invitations.invite_token`. But **NO endpoint anywhere in the backend INSERTs into
  `team_invitations`** — grep of the whole backend shows only SELECT/UPDATE (status transitions)
  plus the CREATE TABLE migration V130. The token is never issued, so accept is unreachable.
- Net: an owner cannot, through the API, create a teammate who can actually authenticate. Only the
  original owner has a `passwordHash` in the tenant.

## Impact
1. **Product:** Tim & Akses invitation is broken end-to-end via API for fresh tenants — cannot add
   any staff. (This is the standing "owner is the only user" state observed in this run.)
2. **Testing:** permission dimension D (role-based 403 enforcement — is a low role actually blocked
   from `/customer-deposits/{id}/refund`, `GET /customer-deposits`, etc.) **cannot be validated
   this session** without raw DB writes (password injection / forged JWT), which are out of bounds.
   So the entire DP run proves NOTHING about permission enforcement; that remains unverified.

## Fix sketch
- Defect 1: role lookup must accept `__SYSTEM__` roles — `WHERE id = $1 AND tenant_id IN ($2,
  '__SYSTEM__')` (or drop the tenant filter for the global role catalog). Audit every other
  tenant-scoped `roles` query for the same assumption.
- Defect 2: wire an invite path that INSERTs `team_invitations` (issue `invite_token`, like the
  signup magic-token in goldenpath.sh) so `accept` can set a password — OR have `team-members/invite`
  itself provision credentials / a set-password token. Decide which flow is canonical (there are
  currently two half-built ones).

## To validate D once fixed
Seed a low-role user via the (fixed) API, obtain their JWT, then assert:
`POST /customer-deposits/{DEPID}/refund` → 403; `GET /customer-deposits` → 403; owner same calls →
not 403. Any 200 for the low role = D not enforced.
