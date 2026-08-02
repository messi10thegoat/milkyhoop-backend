# TICKET: live FE bundle has NO git provenance — exists only inside the running image

**Date:** 2026-08-03  **Severity:** HIGH (live UI is unreproducible if the image is lost)
**Status:** OPEN — blocks any UI walkthrough that must reflect current source. Fix = rebuild from a
pinned source commit (runbook DOCS/runbooks/2026-07-27-batch2-deploy-runbook.md, FE section).

## Facts
- Live FE = container `milkyhoop-dev-frontend-1`, serving bundle **main.5558c404.js**.
- The server FE build tree `/root/milkyhoop-dev/frontend` is a git dir whose HEAD build is
  **main.6a02fcc0.js**; `git ls-files --deleted` shows the committed 6a02fcc0 assets are MISSING
  from the working tree, which instead holds 5558c404 (swapped in manually, uncommitted).
- **NEW FACT (2026-08-03):** `milkyhoop-dev-frontend-1` has **ZERO bind mounts** — assets are
  BAKED INTO THE IMAGE at build time. Therefore the live bundle **5558c404 exists ONLY inside the
  running container image**. It is in NO git tree (HEAD=6a02fcc0, working=deleted/swapped) and in NO
  committed build. **If that image is lost/rebuilt, the currently-running UI cannot be reproduced.**
- FE SOURCE (Mac /Users/antoniwan/milkyhoop/frontend/web) is clean + complete at 2bd845159, node
  v18.20.8, builds via react-scripts, `.env.local REACT_APP_API_URL=` empty (relative), no
  `.env.production`.

## Why this matters
A UI walkthrough on the live 5558c404 proves nothing about current source (unknown provenance), and
the source-of-truth for what users see is a mutable container image, not git. This strengthens the
rebuild-from-pinned-source plan: build FE from a pinned commit on the Mac → deploy fresh assets →
verify hash ≠ 5558c404 → walkthrough. Do NOT `git restore` the server tree (brings back the stale
6a02fcc0, still not a fresh build). Do NOT rebuild the frontend image before capturing/deciding on
5558c404, or the running UI becomes unreproducible.

## Related
Backend deploy #2 (2026-08-03, A1/B1/credit_notes) deliberately did NOT touch the frontend
container or the frontend/ tree (backend-only, ff-only merge not reset --hard) precisely to avoid
disturbing this state.
