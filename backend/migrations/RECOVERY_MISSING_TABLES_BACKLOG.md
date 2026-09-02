# Recovery — missing-table backlog & policy (2026-07-24)

## What was fixed (authoritative — table has DDL somewhere)

Two distinct classes were found and closed:

### A. Table referenced by code, DDL in NO migration (reconstructed from code, arbiter=code)
- **V212** `withholding_tax_records` — PPh report.
- **V213** `chat_attachments` — chat history per-session 500 (OBSERVED in gateway log). Verified real 200 after fix.
- **V214** `sales_invoice_attachments`, `user_explicit_preferences` — user-facing (invoice Lampiran, Tier-2 chat memory). Tables created; NOT yet HTTP-proven. KEPT (chat_attachments proved the class real; both are user-facing with anticipatable failures).
- **~~V215~~ ROLLED BACK 2026-07-25** `master_data_audit_log`, `product_units`, `journal_sequences`, `tool_call_logs` — these were created from a code-grep scan with NO observed failure. Rolled back on owner directive — NOT because the schemas were wrong, but because the precedent ("scan found it → I built it") blurs the line between "recovered from repo" and "invented from grep", and that line is what has saved us repeatedly. Tables dropped (all were empty), migration removed. Moved to backlog below.

### B. "Half-aborted migration" — DDL IS in the repo, migration ran partially
Root cause: the fresh-install runner **continues past a failed statement inside a migration file yet records the file OK** (no `ON_ERROR_STOP` / only file-level success check). So a migration can leave some objects behind.
- **V216** re-applies **V057** tail: `sensitive_data_access`, `login_history`, `audit_retention_policies` (V057 aborted after `audit_logs`).
- **V217** re-applies **V041** tail: `forex_gain_loss` (V041 aborted after `exchange_rates`).

**Authoritative detector for class B (run this, not code-grep):**
```
diff( every "CREATE TABLE [IF NOT EXISTS] <name>" in backend/migrations/  ,  pg_class relkind IN ('r','p') )
```
After V216/V217 this diff is **EMPTY** except `journal_entries_YYYY_MM` (superseded partitions — `journal_entries` is a plain `relkind='r'` table, never partitioned in the live schema). **Class B is CLOSED.**

## Policy for class A going forward (owner directive, 2026-07-24)
**Do NOT create a table speculatively from a code-grep of INSERT columns.** That is the discarded `reconstructed_schema.sql` method. A *missing* table fails LOUD (500 UndefinedTable — obvious); a *wrong-schema* table can accept data SILENTLY and be wrong downstream — strictly worse.

**Create a class-A table only when there is an OBSERVED real failure** (like `chat_attachments`' 500 in the log). Until then: leave it missing → it fails loud if touched → that is the desired signal.

### C. Created on an OBSERVED failure (the policy working as intended)
- **V226** `product_units` — **2026-09-02.** Owner pressed `+ Buat satuan "Kilogram"` in the
  Tambah Item form repeatedly, nothing happened. Measured:
  `to_regclass('public.product_units')` = NULL while `unit_conversions` exists; every endpoint
  in `routers/units.py` touching it returns 500. That is the observed failure the policy below
  requires, so the table was reconstructed with **code as arbiter**, per column, citing line
  numbers in the migration header.
  **The rolled-back V215 schema would NOT have fixed this**: it omitted `is_active` and
  `updated_at`, both of which `units.py` reads (l.66,114,126) and writes (l.258,316) — and its
  own DO-block did not check for them, so it would have reported OK while list/dropdown/update/
  delete still 500'd. Rebuilding from the code beat reusing the archived migration.
  Tested on scratch DB `milkydb_satuan_test`; rollback in
  `V226__create_product_units_ROLLBACK.sql`, tested in both directions.

## BACKLOG — code-referenced, no DDL anywhere, NOT created (fail-loud if touched)
Create only on an observed failure, reconstructing carefully with code as arbiter.

| Table(s) | Module / source | Notes |
|---|---|---|
| `tax_invoices`, `tax_invoice_items`, `tax_invoice_sources`, `nsfp_assignments`, `tax_groups`, `tax_group_items`, `tax_info`, `product_djp_mapping`, `efaktur_exports` | e-Faktur / PKP (tax_invoices.py, efaktur.py, tax_groups.py, pkp_settings.py) | Coherent module — all-or-nothing; several use DYNAMIC SQL (tax_info, tax_invoices, efaktur). Only if tenant is PKP + hits it. |
| `expense_claims`, `expense_claim_lines`, `expense_policies`, `recurring_expenses` | Expense claims (expense_extended.py) | Only if the expense-claims feature is used. |
| `granular_permissions` | Layer-2 permissions (permission_service.py) | Multi-shape INSERT + `module` column + ON CONFLICT — needs careful read. Reachable via Team & Access. |
| `action_patterns`, `chat_telemetry` | Chat learning/telemetry (action_memory.py, telemetry.py) | Fire-and-forget (try/except) — will not hard-500. |
| `master_data_audit_log` | audit_log.py + vendors.py (master-data edit audit) | Ex-V215 (rolled back). Create on observed failure. |
| `journal_sequences` | fixed_assets.py (per-tenant counter) | Ex-V215 (rolled back). Create on observed failure. |
| `tool_call_logs` | unified_agent/observability.py (fire-and-forget) | Ex-V215 (rolled back). Will not hard-500. |
| `kds_order_history`, `menu_item_modifiers`, `recipe_modifier_groups`, `recipe_modifier_options`, `reservations` | Resto/POS (kds.py, recipes.py, tables.py) | IRRELEVANT to a konveksi/garment tenant. Do not create. |
| `notifications` | **Go notification-service** (services/notification-service) | Other-service-owned — its schema belongs to that service's own migrations, NOT the gateway. |
| `policy_audit_log` | **policy_engine service** | Other-service-owned. |

## Recommended durable fix (not done — owner decision)
Harden the fresh-install runner so a failed statement inside a migration fails the migration (e.g., run each file with `psql -v ON_ERROR_STOP=1 --single-transaction` and treat non-zero exit as FAIL). This prevents the class-B recurrence on the next from-empty rebuild.
