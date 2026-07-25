# Issue: permission enforcement has two layers with OPPOSITE failure defaults

## Observation
- Layer 1 — PermissionMiddleware (middleware/permission_middleware.py): an UNMAPPED route
  (no ROUTE_PERMISSIONS match) falls through -> `call_next` -> ALLOW. **fail-OPEN.**
  Its exception handler also allows ("Fail-open for now", ~line 360).
- Layer 2 — PolicyEngineClient.can (services/policy_engine_client.py): on exception
  `return False`, and module-not-granted -> DENY. **fail-CLOSED.**

## Why it matters
System security under error/omission depends on WHICH layer happens to handle the request.
A route present in ROUTE_PERMISSIONS but hitting a policy-engine error -> denied; a route
simply not listed -> allowed. Adding a route to ROUTE_PERMISSIONS moves it from the fail-open
layer to the fail-closed layer — which is exactly why, after the B1 deposit mapping,
admin/accountant business roles are denied deposits until granted (only OWNER bypasses).

## Proposals (NOT implemented — require owner decision + prerequisites)
1. Flip Layer 1 to deny: unmapped route -> 403, and the exception handler -> 403.
   PREREQUISITE: a CI route-coverage check that every registered API route has a
   ROUTE_PERMISSIONS entry (or is on an explicit SKIP allowlist). Without it, flipping
   would 403 every currently-unmapped endpoint (many exist) -> broad breakage.
2. CI route-coverage check: enumerate FastAPI routes at build; fail if any lack a
   permission mapping or SKIP entry. Ship this FIRST, then flip.

## Related
- B1 deposit mapping: commit that added CUSTOMER_DEPOSIT/VENDOR_DEPOSIT to ROUTE_PERMISSIONS.
- VALUE-DOMAIN-DRIFT scan extension (separate hypothesis, 0 confirmed instances) — see
  scripts/schema-contract/SCAN_REPORT.md.
