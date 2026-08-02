# Issue (HIGH): authentication has no revocation — session authority disabled

## Observation
auth_middleware.py: the session-authority (kill-switch) check is behind `if False:  # DISABLED
FOR DEV - if device_id and device_type:`. Disabled 2026-01-28 by commit 1293d388 (milkyhoop-bot,
"chore: disable single session enforcement for development"). Auth therefore accepts any
signature-valid JWT until its natural expiry.

## Consequence
- Logout revokes nothing (JWT stays valid to expiry).
- A leaked/stolen token cannot be revoked.
- Device replacement ("logged in elsewhere") is not enforced.
- session_manager (Redis session authority) is dead code as long as this is off — and Redis is
  currently down anyway, so even re-enabling needs the redis parse bug fixed first.

## Why it matters now
We just closed a permission fail-open (B1), but the AUTH layer beneath it has no kill switch.
"DEV" is running behind Cloudflare on milkyhoop.com — this is production posture.

## Not a FASE-4 blocker
The harness needs auth to WORK, which it does (JWT signature). This ticket is about revocation,
not availability. Re-enable requires: redis parse bug fixed (ticket 1) + verifying every issued
JWT carries device_id/device_type (the `if False` guard also protects legacy JWTs without device
claims — re-enabling naively would 401 them).
