# Runbook: DP branch merge + deploy + apply (fix/dp-readiness)

Carries: V218 (AP fix + 2 UNIQUE), V219 (quotes cols), V220 (products tax FK), B1b lock fix,
B1 deposit permission mapping, sentinels/migrate.sh, schema-contract scan + ratchet. All DB
migrations are additive + idempotent.

## PRE-MERGE CHECKLIST
- [ ] Fresh live milkydb snapshot (pg_dump | gzip | gpg) + record current master commit (f71db830).
- [ ] Reverse-DDL script ready:
      V220: ALTER TABLE products DROP COLUMN sales_tax_id, DROP COLUMN purchase_tax_id;
      V219: ALTER TABLE quotes DROP COLUMN opening_text, closing_text, payment_bank_name,
            payment_account_number, payment_account_holder;
      V218: CREATE OR REPLACE FUNCTION compute_ap_outstanding(...) <old body>;
            ALTER TABLE customer_deposit_applications DROP CONSTRAINT uq_cda_journal_id;
            ALTER TABLE vendor_deposit_applications   DROP CONSTRAINT uq_vda_journal_id;
      then: DELETE FROM schema_migrations WHERE version IN ('V218__...','V219__...','V220__...');
- [ ] Delta deploy confirmed clean: only our branch commits vs f71db830 (verified: 0 other-session
      commits on origin/master).
- [ ] DEPLOY COMMAND IS EXPLICIT: `docker compose -f docker-compose.yml up -d api_gateway`.
      NEVER a bare `docker compose up -d` (touches the `frontend` service) and NEVER `--build`
      (the frontend service has build: context ./frontend/web, whose src/ is missing 4 .tsx ->
      broken/incomplete FE build). FE image is NOT part of this deploy.

## APPLY (after merge to master)
- [ ] In main tree: git fetch + checkout master + reset --hard origin/master (guard: status clean).
- [ ] Apply migrations via pipeline: `PGDB=milkydb migrate.sh apply` (V218/V219/V220 -> applied).
- [ ] Clear __pycache__, then `docker compose up -d api_gateway` (reloads code).

## POST-DEPLOY VERIFY
- [ ] /health /ready /version -> 200.
- [ ] migrate.sh verify -> "no drift", 214 tracked (211 + V218/V219/V220), sentinels skipped.
- [ ] compute_ap sanity (0-tenant -> empty, no error).

## ROLLBACK ORDER
1. Revert CODE first: main tree `git reset --hard <pre-deploy master>` + `up -d api_gateway`.
   Additive migrations may stay (old code ignores new columns; UNIQUE never conflicts with
   1-journal-per-application code).
2. Only if a migration itself is the problem: run its reverse-DDL (above) + DELETE its
   schema_migrations row.

## FASE-4 ACCEPTANCE GATES (harness must prove these; not "nice to have")
- [ ] Ledger closing invariant (the full steps 0-9 spec) — via scripted harness (owner).
- [ ] NON-OWNER PROBE (validates B1 — owner-run harness CANNOT, owner bypasses):
      seed one low business-role user; POST /api/customer-deposits/{id}/refund -> EXPECT 403;
      GET /api/customer-deposits -> EXPECT 403 (module not granted). Without this, D ships with
      zero runtime proof of the one thing it does (deny the unauthorized).
- [ ] Step-1 handler gate: POST /quotes-with-items -> 201 (quote-number generator has never
      produced a number; 0 quotes in any DB).
- [ ] Step-0 gate: GET /items/autocomplete -> 200.

## FASE-5 GATES (two)
1. Ledger via the FASE-4 harness (backend truth).
2. ONE UI pass with the FE BUILT FROM A PINNED COMMIT (the live bundle main.5558c404.js is
   unknown-provenance; rebuild frontend image from a known commit before claiming "works for a
   user"). Open question to resolve here: does the live UI still have the 4 components whose
   .tsx are deleted on the server tree (ChatPanel, PurchaseInvoiceForm, AddItemForm)?

---
## ADDENDUM (pre-flight)

### Shadowing test (pre-flight #1) — PASS
Full ROUTE_PERMISSIONS first-match, PRE vs POST the 25-entry insert, over a non-deposit sample
(sales-invoices list/detail/post/void/payments/fulfillments/fulfill, receive-payments, bills,
bill-payments, quotes, items, autocomplete, products, journals, customers, vendors, expenses):
PRE=183 rules, POST=208 (+25 exactly), sample diffs = 0. No existing route re-resolves.

### FASE-4 gate — voided_reason (pre-flight #3), 2-sided, EXPLICIT
Commit f5cd41a5 (fulfillments voided_reason) was never proven via HTTP (needs an invoice in a
tenant; live is 0-tenant). Gate for FASE 4:
  GET /api/sales-invoices/{id}/fulfillments -> 200 AND:
    - invoice not yet shipped  -> shippable items appear
    - invoice fully shipped    -> "semua dikirim" (nothing shippable)
If not asserted here it passes as an assumption — the exact failure mode that started this session.

### Harness DB mechanism (pre-flight #2) — DECISION PENDING (owner)
Finding: the gateway is NOT the only consumer of milkydb — auth_service (grpc) + other
microservices share it. A harness signup/login round-trips auth_service, so pointing ONLY the
gateway at a clone does not isolate; the WHOLE stack must repoint. Options:
  (A) RECOMMENDED — run FASE-4 harness on live milkydb as a THROWAWAY tenant, then DELETE it
      (restore 0-tenant). Tests the exact shipped code + real prod state; simplest; 0-tenant is
      restorable. Rationale it's now acceptable: the 0-tenant window's original value
      (rebuild-vs-diff) is already discharged — the column-diff proved live==saved (0 drift) and
      sentinels are in place. Revert = delete the tenant (cascade) post-run.
  (B) Clone milkydb + repoint the WHOLE dev stack (gateway + auth + grpc) to the clone; run
      harness; repoint back. True isolation, heavier, downtime for milkyhoop.com (0-tenant so no
      users). Revert = recreate stack against milkydb.
  (C) Parallel mini-stack (2nd gateway + 2nd auth + clone) on separate ports — cleanest isolation,
      most setup.
Decide before FASE 4 starts (does not block this deploy).
