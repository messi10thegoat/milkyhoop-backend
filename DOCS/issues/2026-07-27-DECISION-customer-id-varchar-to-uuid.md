# DECISION (owner, after team-invite): convert customer_id VARCHAR → uuid + add FK

**Date filed:** 2026-07-27  **Status:** PROPOSED — do NOT execute without owner GO.

## Proposal
Convert `customer_deposits.customer_id` AND `credit_notes.customer_id` from `character varying` to
`uuid`, and add a real FK to `customers(id)`. Every other customer FK is already `uuid`; these two
are the lone drift.

## Why (class, not instance)
- Today there is NO foreign key at all on these columns → orphan deposits/credit-notes are possible;
  zero referential integrity.
- The type drift means every JOIN to `customers`/`sales_invoices` needs a cast (indexes unused), and
  every NEW call-site risks repeating the UUID→VARCHAR 500. This session already hit it TWICE
  (applicable-deposits = A1 fixed; credit_notes list filter = filed). Patching per-site fixes
  instances; converting the column fixes the class — same argument as the V218 UNIQUE constraint.

## WINDOW
Cheapest NOW: milkydb has zero real tenants, so the migration is a pure type change with no data to
preserve/backfill risk. Once early adopters exist, it needs careful backfill + downtime. Do it
before go-live or not cheaply at all.

## RISK — must grep ALL readers first (owner pre-step)
After conversion asyncpg returns a `uuid.UUID` object (not str) when READING the column. Code that
compares it as a string, or returns it raw where a str is expected, can break. Initial reader grep:
- `customer_deposits.py:398, :569, :2402` return `row["customer_id"]` RAW in a response dict →
  post-conversion emits a UUID; FastAPI's jsonable_encoder stringifies it, but CONFIRM (these 3 are
  the only raw-return sites).
- `sales_invoices.py` sites already wrap in `str(...)` — safe.
- No `== "<str>"` comparisons on customer_id found — no equality breakage seen.
No string-comparison breakage found in the initial pass, but a FULL grep of every reader
(routers + services + chat action executors) is REQUIRED before executing.

## Suggested execution (when GO)
1. Full reader grep (above) — fix any raw/string-compare readers to tolerate uuid.
2. Migration Vxxx: `ALTER TABLE ... ALTER COLUMN customer_id TYPE uuid USING customer_id::uuid;`
   for both tables, then `ADD CONSTRAINT ... FOREIGN KEY (customer_id) REFERENCES customers(id)`.
   (Fetch-before-apply the V-number — shared milkydb, see migration-number-reservation.)
3. Revert the A1 `str(...)` and credit_notes `str(...)` shims (no longer needed once uuid).
4. Run run_all + credit-notes smoke on an isolated test gateway before deploy.

## Sequencing
Owner decision, AFTER team-invite (batch #2). Bundle with the credit_notes str() fix so the whole
class closes in one migration rather than two more instance patches.
