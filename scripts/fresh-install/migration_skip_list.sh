#!/bin/bash
# GENERATED from run_migrations_v9.sh SKIP_REASON — historical skips (do not hand-edit; regenerate).
declare -A SKIP_REASON
SKIP_REASON["V010__accounting_kernel_schema.sql"]="SKIP — partitioned journal_entries with composite PK/FK; superseded by V012+V013"
SKIP_REASON["V011__seed_default_coa.sql"]="SKIP — seed function for V010 schema; V013 provides full replacement"
SKIP_REASON["V012__fix_accounting_tenant_id_type.sql"]="SKIP — alters V010's partitioned chart_of_accounts/journal_entries which are skipped; V013 creates correct non-partitioned tables from scratch"
SKIP_REASON["V074__partition_automation.sql"]="SKIP — journal_entries non-partitioned (V013); partition functions inapplicable"
SKIP_REASON["V128__fix_compute_ar_adjustments_invoice_reversal.sql"]="SKIP — unquoted SQL identifiers (RECEIVABLE, POSTED); function superseded by V170 which ran OK"
SKIP_REASON["V139__rule8_invariant_fix.sql"]="SKIP — unquoted SQL identifiers (POSTED, INVOICE_REVERSAL); function superseded by V169 which ran OK"
SKIP_REASON["V006__add_search_gin_indexes.sql"]="SKIP — GIN indexes on customers/products; customers not yet created at V006, indexes added later"
SKIP_REASON["V020__ap_reconciliation.sql"]="SKIP — references tenant.nama (old Prisma Tenant schema), sejarah mati"
SKIP_REASON["V057__audit_trail.sql"]="SKIP — audit_logs created in Step 0 stub; stub provides functional audit_logs; V057 SIBLING tables sensitive_data_access/login_history/audit_retention_policies salvaged by V216 (idempotent)"
SKIP_REASON["V101__backfill_dual_status_bill_payments.sql"]="SKIP — backfill references old status column that never existed in fresh install"
SKIP_REASON["V125__products_tax_indexes.sql"]="SKIP — indexes on sales_tax_id (column is sales_tax in our schema), sejarah mati"
SKIP_REASON["V008__recalculate_persediaan_stock.sql"]="SKIP — references p.base_unit; RE-EVALUASI setelah V007 dicabut"
SKIP_REASON["V194__create_unit_conversions_item_pricing.sql"]="SKIP — references p.base_unit; RE-EVALUASI setelah V007 dicabut"
SKIP_REASON["V041__multi_currency.sql"]="SKIP — currencies+exchange_rates created in Step 0 stub; DML UPDATEs fail on empty fresh DB; forex_gain_loss salvaged by V217 (idempotent)"
SKIP_REASON["V007__add_unit_conversion_fields.sql"]="SKIP — DIUJI 2026-07-24: gagal ERROR column it.satuan does not exist (item_transaksi era Prisma). Konsekuensi: products.base_unit tidak pernah dibuat -> V008/V194 ikut mati."
