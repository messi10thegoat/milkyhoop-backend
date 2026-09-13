# TIKET — Merging customers (`POST /api/customers/merge`) has never succeeded

**Status:** OPEN · measured, not repaired · 2026-09-14 · found by the V247 unit (pelanggan uuid + composite FK)

## Claim (hypothesis per Law 34 until the merge unit traces every layer)
`merge_customers` (routers/customers.py, born in commit `74372171` on 2026-01-28) is **dead from birth**:
the transaction always fails and rolls back, so nothing is ever merged.

## Measurement (2026-09-14, milkydb, all tenants)
| Trace a successful merge would leave | Count |
|---|---|
| `customer_activities` with `type='merge'` | **0** |
| `customers.deleted_at IS NOT NULL` | **0** |
| `customers.is_active = false` | **0** |
| `audit_logs` mentioning merge | **0** |

The handler writes all three of the first traces in the same transaction. Zero on all three since the earliest customer (2026-08-09 on this DB) means there has been **no successful merge on this DB**.
Limit of this measurement: this DB is the result of the fresh-install recovery (2026-07-24). Attempts before that date cannot be seen. "Never" means "not since this DB existed", combined with the code defect below, which has been present since birth.

## Known broken layers (not exhaustive: stop when the error shifts)
1. `UPDATE customers SET … deleted_by = $3` passes `ctx["user_id"]` (UUID) into a **varchar** column. This fails at encoding.
2. Before V247, the `customer_deposits` / `accounts_receivable` branches compared varchar with uuid. Deploy 1 changed this to text-on-text. After V247, `credit_notes`/`customer_deposits` are uuid; this branch has not been rerun.
3. Unverified: the `customer_activities` columns (`type`, `actor_id`) against information_schema.

## Composite FK impact (V247)
Merging within one tenant is still allowed by `fk_*_customer_tenant`. A merge that tries to move documents to **another tenant's** customer will now be rejected by the DB (23503). That is the desired behaviour.

## Acceptance for the merge unit
Trace all layers with the handler + FakePool in ROLLBACK, run until 200. Then check:
- document counts per table move from source to target, exactly;
- the source customer is inactive;
- one activity row per source;
- target from another tenant → 400/23503;
- sabotage on one branch → the gate turns red.
