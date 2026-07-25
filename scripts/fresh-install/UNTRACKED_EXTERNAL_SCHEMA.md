# Untracked-external schema (sentinel rows in schema_migrations)

The fresh-install recipe creates schema OUTSIDE the VNNN migration files. Those objects have
no per-file provenance, so `schema_migrations` would otherwise look complete (209 tracked)
while silently omitting them. Two sentinel rows (status `untracked-external`, applied_by
`sentinel`) mark this knowledge boundary. Their `checksum` is the sha256 of the creating
script, so edits to those scripts are detectable.

## STEP0_STUB — run_migrations_v9.sh Step-0 stub (CREATE TABLE, pre-VNNN)
"Tenant", "User", "UserSecurity", audit_logs, chat_messages, chat_session_state,
chat_sessions, currencies, employees, exchange_rates, item_transaksi, outbox,
payroll_allocations, payroll_runs, persediaan, products, suppliers, transaksi_harian,
unit_conversions.

## GAP_PATCH — gap_patch.sh (post-VNNN patch)
CREATE TABLE: "Account", "Session", "VerificationToken", document_tax_lines,
payroll_allocations, payroll_runs.
ALTER TABLE (adds columns to existing): "Account", "Session", bill_payments, bills,
expenses, journal_entries, receive_payments, sales_invoices.

## Status / backlog
- Sentinel rows inserted on live milkydb (211 total: 209 tracked + 2 sentinels). verify OK.
- migrate.sh: ensure_table CHECK allows `untracked-external`; verify() excludes these rows
  from the orphan check (no V*.sql maps to them by design).
- BACKLOG (deferred, post-E2E): convert STEP0_STUB + gap_patch schema into real VNNN files
  and bring build_fresh.sh into the repo. Deferred because none of this is on the E2E steps
  0-9 path and the verification window does not close (FASE 4/5 use a scratch clone; live
  stays 0-tenant). Reversal of the sentinels: DELETE the 2 rows + restore the 2-value CHECK.
