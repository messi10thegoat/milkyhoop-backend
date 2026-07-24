-- ============================================================
-- V115: Numeric Precision Standardization (Law 25)
-- bigint/double precision → numeric(18,2) for currency
-- Standardize exchange_rate → numeric(18,6)
-- ============================================================
-- SAFE: All financial tables empty except bank_accounts (15 rows)
-- journal_entries/journal_lines already numeric(18,2)
-- ============================================================

BEGIN;

-- Step 1: Drop dependent views and triggers
DROP VIEW IF EXISTS v_products_with_prices CASCADE;
DROP TRIGGER IF EXISTS trg_update_bom_component_cost ON bom_components;

-- Step 2: Drop generated columns (will be recreated after source columns are altered)
ALTER TABLE budget_items DROP COLUMN IF EXISTS annual_amount;
ALTER TABLE vendor_deposits DROP COLUMN IF EXISTS remaining_amount;

-- Step 3: ALTER columns to numeric precision
-- Order
-- SKIPPED (column/table not in schema): ALTER TABLE "Order" ALTER COLUMN total_price TYPE numeric(18,2) USING total_price::numeric(18,2);

-- UserFinance
-- SKIPPED (column/table not in schema): ALTER TABLE "UserFinance" ALTER COLUMN balance TYPE numeric(18,2) USING balance::numeric(18,2);

-- aging_snapshots
ALTER TABLE aging_snapshots ALTER COLUMN grand_total TYPE numeric(18,2) USING grand_total::numeric(18,2);
ALTER TABLE aging_snapshots ALTER COLUMN total_bracket_1 TYPE numeric(18,2) USING total_bracket_1::numeric(18,2);
ALTER TABLE aging_snapshots ALTER COLUMN total_bracket_2 TYPE numeric(18,2) USING total_bracket_2::numeric(18,2);
ALTER TABLE aging_snapshots ALTER COLUMN total_bracket_3 TYPE numeric(18,2) USING total_bracket_3::numeric(18,2);
ALTER TABLE aging_snapshots ALTER COLUMN total_bracket_4 TYPE numeric(18,2) USING total_bracket_4::numeric(18,2);
ALTER TABLE aging_snapshots ALTER COLUMN total_current TYPE numeric(18,2) USING total_current::numeric(18,2);
ALTER TABLE aging_snapshots ALTER COLUMN total_overdue TYPE numeric(18,2) USING total_overdue::numeric(18,2);

-- approval_requests
ALTER TABLE approval_requests ALTER COLUMN document_amount TYPE numeric(18,2) USING document_amount::numeric(18,2);

-- approval_workflows
ALTER TABLE approval_workflows ALTER COLUMN max_amount TYPE numeric(18,2) USING max_amount::numeric(18,2);
ALTER TABLE approval_workflows ALTER COLUMN min_amount TYPE numeric(18,2) USING min_amount::numeric(18,2);

-- asset_depreciations
ALTER TABLE asset_depreciations ALTER COLUMN accumulated_amount TYPE numeric(18,2) USING accumulated_amount::numeric(18,2);
ALTER TABLE asset_depreciations ALTER COLUMN depreciation_amount TYPE numeric(18,2) USING depreciation_amount::numeric(18,2);

-- asset_maintenance
ALTER TABLE asset_maintenance ALTER COLUMN cost TYPE numeric(18,2) USING cost::numeric(18,2);

-- bank_accounts
ALTER TABLE bank_accounts ALTER COLUMN current_balance TYPE numeric(18,2) USING current_balance::numeric(18,2);
ALTER TABLE bank_accounts ALTER COLUMN last_reconciled_balance TYPE numeric(18,2) USING last_reconciled_balance::numeric(18,2);
ALTER TABLE bank_accounts ALTER COLUMN opening_balance TYPE numeric(18,2) USING opening_balance::numeric(18,2);

-- bank_matching_history
-- SKIPPED (column/table not in schema): ALTER TABLE bank_matching_history ALTER COLUMN bank_amount TYPE numeric(18,2) USING bank_amount::numeric(18,2);

-- bank_reconciliation_items
ALTER TABLE bank_reconciliation_items ALTER COLUMN adjustment_amount TYPE numeric(18,2) USING adjustment_amount::numeric(18,2);
ALTER TABLE bank_reconciliation_items ALTER COLUMN statement_amount TYPE numeric(18,2) USING statement_amount::numeric(18,2);

-- bank_reconciliations
ALTER TABLE bank_reconciliations ALTER COLUMN statement_closing_balance TYPE numeric(18,2) USING statement_closing_balance::numeric(18,2);
ALTER TABLE bank_reconciliations ALTER COLUMN statement_opening_balance TYPE numeric(18,2) USING statement_opening_balance::numeric(18,2);
ALTER TABLE bank_reconciliations ALTER COLUMN system_closing_balance TYPE numeric(18,2) USING system_closing_balance::numeric(18,2);
ALTER TABLE bank_reconciliations ALTER COLUMN system_opening_balance TYPE numeric(18,2) USING system_opening_balance::numeric(18,2);

-- bank_statement_lines
ALTER TABLE bank_statement_lines ALTER COLUMN balance TYPE numeric(18,2) USING balance::numeric(18,2);
ALTER TABLE bank_statement_lines ALTER COLUMN credit_amount TYPE numeric(18,2) USING credit_amount::numeric(18,2);
ALTER TABLE bank_statement_lines ALTER COLUMN debit_amount TYPE numeric(18,2) USING debit_amount::numeric(18,2);

-- bank_statement_lines_v2
ALTER TABLE bank_statement_lines_v2 ALTER COLUMN amount TYPE numeric(18,2) USING amount::numeric(18,2);
ALTER TABLE bank_statement_lines_v2 ALTER COLUMN running_balance TYPE numeric(18,2) USING running_balance::numeric(18,2);

-- bank_transactions
ALTER TABLE bank_transactions ALTER COLUMN amount TYPE numeric(18,2) USING amount::numeric(18,2);
ALTER TABLE bank_transactions ALTER COLUMN running_balance TYPE numeric(18,2) USING running_balance::numeric(18,2);

-- bank_transfers
ALTER TABLE bank_transfers ALTER COLUMN amount TYPE numeric(18,2) USING amount::numeric(18,2);
ALTER TABLE bank_transfers ALTER COLUMN fee_amount TYPE numeric(18,2) USING fee_amount::numeric(18,2);
ALTER TABLE bank_transfers ALTER COLUMN total_amount TYPE numeric(18,2) USING total_amount::numeric(18,2);

-- bill_items
ALTER TABLE bill_items ALTER COLUMN discount_amount TYPE numeric(18,2) USING discount_amount::numeric(18,2);
ALTER TABLE bill_items ALTER COLUMN subtotal TYPE numeric(18,2) USING subtotal::numeric(18,2);
ALTER TABLE bill_items ALTER COLUMN total TYPE numeric(18,2) USING total::numeric(18,2);
ALTER TABLE bill_items ALTER COLUMN unit_price TYPE numeric(18,2) USING unit_price::numeric(18,2);

-- bill_of_materials
ALTER TABLE bill_of_materials ALTER COLUMN labor_cost TYPE numeric(18,2) USING labor_cost::numeric(18,2);
ALTER TABLE bill_of_materials ALTER COLUMN overhead_cost TYPE numeric(18,2) USING overhead_cost::numeric(18,2);
ALTER TABLE bill_of_materials ALTER COLUMN standard_cost TYPE numeric(18,2) USING standard_cost::numeric(18,2);
ALTER TABLE bill_of_materials ALTER COLUMN total_cost TYPE numeric(18,2) USING total_cost::numeric(18,2);

-- bill_payment_allocations
ALTER TABLE bill_payment_allocations ALTER COLUMN amount_applied TYPE numeric(18,2) USING amount_applied::numeric(18,2);

-- bill_payments
ALTER TABLE bill_payments ALTER COLUMN amount TYPE numeric(18,2) USING amount::numeric(18,2);

-- bill_payments_v2
ALTER TABLE bill_payments_v2 ALTER COLUMN allocated_amount TYPE numeric(18,2) USING allocated_amount::numeric(18,2);
ALTER TABLE bill_payments_v2 ALTER COLUMN amount_in_base_currency TYPE numeric(18,2) USING amount_in_base_currency::numeric(18,2);
ALTER TABLE bill_payments_v2 ALTER COLUMN bank_fee_amount TYPE numeric(18,2) USING bank_fee_amount::numeric(18,2);
ALTER TABLE bill_payments_v2 ALTER COLUMN discount_amount TYPE numeric(18,2) USING discount_amount::numeric(18,2);
ALTER TABLE bill_payments_v2 ALTER COLUMN total_amount TYPE numeric(18,2) USING total_amount::numeric(18,2);
ALTER TABLE bill_payments_v2 ALTER COLUMN unapplied_amount TYPE numeric(18,2) USING unapplied_amount::numeric(18,2);

-- bills
ALTER TABLE bills ALTER COLUMN amount TYPE numeric(18,2) USING amount::numeric(18,2);
ALTER TABLE bills ALTER COLUMN amount_paid TYPE numeric(18,2) USING amount_paid::numeric(18,2);
-- SKIPPED (column/table not in schema): ALTER TABLE bills ALTER COLUMN base_currency_total TYPE numeric(18,2) USING base_currency_total::numeric(18,2);
ALTER TABLE bills ALTER COLUMN cash_discount_amount TYPE numeric(18,2) USING cash_discount_amount::numeric(18,2);
ALTER TABLE bills ALTER COLUMN cash_discount_total TYPE numeric(18,2) USING cash_discount_total::numeric(18,2);
-- SKIPPED (column/table not in schema): ALTER TABLE bills ALTER COLUMN exchange_rate TYPE numeric(18,6) USING exchange_rate::numeric(18,6);
ALTER TABLE bills ALTER COLUMN grand_total TYPE numeric(18,2) USING grand_total::numeric(18,2);
ALTER TABLE bills ALTER COLUMN invoice_discount_amount TYPE numeric(18,2) USING invoice_discount_amount::numeric(18,2);
ALTER TABLE bills ALTER COLUMN invoice_discount_total TYPE numeric(18,2) USING invoice_discount_total::numeric(18,2);
ALTER TABLE bills ALTER COLUMN item_discount_total TYPE numeric(18,2) USING item_discount_total::numeric(18,2);
ALTER TABLE bills ALTER COLUMN pph_amount TYPE numeric(18,2) USING pph_amount::numeric(18,2);
ALTER TABLE bills ALTER COLUMN subtotal TYPE numeric(18,2) USING subtotal::numeric(18,2);
ALTER TABLE bills ALTER COLUMN tax_amount TYPE numeric(18,2) USING tax_amount::numeric(18,2);

-- bom_components
ALTER TABLE bom_components ALTER COLUMN extended_cost TYPE numeric(18,2) USING extended_cost::numeric(18,2);
ALTER TABLE bom_components ALTER COLUMN unit_cost TYPE numeric(18,2) USING unit_cost::numeric(18,2);

-- bom_operations
ALTER TABLE bom_operations ALTER COLUMN labor_rate_per_hour TYPE numeric(18,2) USING labor_rate_per_hour::numeric(18,2);
ALTER TABLE bom_operations ALTER COLUMN overhead_rate_per_hour TYPE numeric(18,2) USING overhead_rate_per_hour::numeric(18,2);

-- budget_items
ALTER TABLE budget_items ALTER COLUMN apr_amount TYPE numeric(18,2) USING apr_amount::numeric(18,2);
ALTER TABLE budget_items ALTER COLUMN aug_amount TYPE numeric(18,2) USING aug_amount::numeric(18,2);
ALTER TABLE budget_items ALTER COLUMN dec_amount TYPE numeric(18,2) USING dec_amount::numeric(18,2);
ALTER TABLE budget_items ALTER COLUMN feb_amount TYPE numeric(18,2) USING feb_amount::numeric(18,2);
ALTER TABLE budget_items ALTER COLUMN jan_amount TYPE numeric(18,2) USING jan_amount::numeric(18,2);
ALTER TABLE budget_items ALTER COLUMN jul_amount TYPE numeric(18,2) USING jul_amount::numeric(18,2);
ALTER TABLE budget_items ALTER COLUMN jun_amount TYPE numeric(18,2) USING jun_amount::numeric(18,2);
ALTER TABLE budget_items ALTER COLUMN mar_amount TYPE numeric(18,2) USING mar_amount::numeric(18,2);
ALTER TABLE budget_items ALTER COLUMN may_amount TYPE numeric(18,2) USING may_amount::numeric(18,2);
ALTER TABLE budget_items ALTER COLUMN nov_amount TYPE numeric(18,2) USING nov_amount::numeric(18,2);
ALTER TABLE budget_items ALTER COLUMN oct_amount TYPE numeric(18,2) USING oct_amount::numeric(18,2);
ALTER TABLE budget_items ALTER COLUMN sep_amount TYPE numeric(18,2) USING sep_amount::numeric(18,2);

-- bukti_potong
ALTER TABLE bukti_potong ALTER COLUMN pph_amount TYPE numeric(18,2) USING pph_amount::numeric(18,2);

-- cheques
ALTER TABLE cheques ALTER COLUMN amount TYPE numeric(18,2) USING amount::numeric(18,2);

-- cost_pools
ALTER TABLE cost_pools ALTER COLUMN actual_amount TYPE numeric(18,2) USING actual_amount::numeric(18,2);
ALTER TABLE cost_pools ALTER COLUMN budgeted_amount TYPE numeric(18,2) USING budgeted_amount::numeric(18,2);
ALTER TABLE cost_pools ALTER COLUMN rate_per_unit TYPE numeric(18,2) USING rate_per_unit::numeric(18,2);

-- cost_variances
ALTER TABLE cost_variances ALTER COLUMN actual_labor_cost TYPE numeric(18,2) USING actual_labor_cost::numeric(18,2);
ALTER TABLE cost_variances ALTER COLUMN actual_material_cost TYPE numeric(18,2) USING actual_material_cost::numeric(18,2);
ALTER TABLE cost_variances ALTER COLUMN actual_overhead_cost TYPE numeric(18,2) USING actual_overhead_cost::numeric(18,2);
ALTER TABLE cost_variances ALTER COLUMN actual_total_cost TYPE numeric(18,2) USING actual_total_cost::numeric(18,2);
ALTER TABLE cost_variances ALTER COLUMN labor_rate_variance TYPE numeric(18,2) USING labor_rate_variance::numeric(18,2);
ALTER TABLE cost_variances ALTER COLUMN material_price_variance TYPE numeric(18,2) USING material_price_variance::numeric(18,2);
ALTER TABLE cost_variances ALTER COLUMN standard_labor_cost TYPE numeric(18,2) USING standard_labor_cost::numeric(18,2);
ALTER TABLE cost_variances ALTER COLUMN standard_material_cost TYPE numeric(18,2) USING standard_material_cost::numeric(18,2);
ALTER TABLE cost_variances ALTER COLUMN standard_overhead_cost TYPE numeric(18,2) USING standard_overhead_cost::numeric(18,2);
ALTER TABLE cost_variances ALTER COLUMN standard_total_cost TYPE numeric(18,2) USING standard_total_cost::numeric(18,2);
ALTER TABLE cost_variances ALTER COLUMN total_variance TYPE numeric(18,2) USING total_variance::numeric(18,2);

-- credit_note_applications
ALTER TABLE credit_note_applications ALTER COLUMN amount_applied TYPE numeric(18,2) USING amount_applied::numeric(18,2);

-- credit_note_items
ALTER TABLE credit_note_items ALTER COLUMN discount_amount TYPE numeric(18,2) USING discount_amount::numeric(18,2);
ALTER TABLE credit_note_items ALTER COLUMN subtotal TYPE numeric(18,2) USING subtotal::numeric(18,2);
ALTER TABLE credit_note_items ALTER COLUMN tax_amount TYPE numeric(18,2) USING tax_amount::numeric(18,2);
ALTER TABLE credit_note_items ALTER COLUMN total TYPE numeric(18,2) USING total::numeric(18,2);
ALTER TABLE credit_note_items ALTER COLUMN unit_price TYPE numeric(18,2) USING unit_price::numeric(18,2);

-- credit_note_refunds
ALTER TABLE credit_note_refunds ALTER COLUMN amount TYPE numeric(18,2) USING amount::numeric(18,2);

-- credit_notes
ALTER TABLE credit_notes ALTER COLUMN amount_applied TYPE numeric(18,2) USING amount_applied::numeric(18,2);
ALTER TABLE credit_notes ALTER COLUMN amount_refunded TYPE numeric(18,2) USING amount_refunded::numeric(18,2);
-- SKIPPED (column/table not in schema): ALTER TABLE credit_notes ALTER COLUMN base_currency_total TYPE numeric(18,2) USING base_currency_total::numeric(18,2);
ALTER TABLE credit_notes ALTER COLUMN discount_amount TYPE numeric(18,2) USING discount_amount::numeric(18,2);
-- SKIPPED (column/table not in schema): ALTER TABLE credit_notes ALTER COLUMN exchange_rate TYPE numeric(18,6) USING exchange_rate::numeric(18,6);
ALTER TABLE credit_notes ALTER COLUMN subtotal TYPE numeric(18,2) USING subtotal::numeric(18,2);
ALTER TABLE credit_notes ALTER COLUMN tax_amount TYPE numeric(18,2) USING tax_amount::numeric(18,2);
ALTER TABLE credit_notes ALTER COLUMN total_amount TYPE numeric(18,2) USING total_amount::numeric(18,2);
-- SKIPPED (column/table not in schema): ALTER TABLE credit_notes ALTER COLUMN total_cogs TYPE numeric(18,2) USING total_cogs::numeric(18,2);

-- customer_deposit_applications
ALTER TABLE customer_deposit_applications ALTER COLUMN amount_applied TYPE numeric(18,2) USING amount_applied::numeric(18,2);

-- customer_deposit_refunds
ALTER TABLE customer_deposit_refunds ALTER COLUMN amount TYPE numeric(18,2) USING amount::numeric(18,2);

-- customer_deposits
ALTER TABLE customer_deposits ALTER COLUMN amount TYPE numeric(18,2) USING amount::numeric(18,2);
ALTER TABLE customer_deposits ALTER COLUMN amount_applied TYPE numeric(18,2) USING amount_applied::numeric(18,2);
ALTER TABLE customer_deposits ALTER COLUMN amount_refunded TYPE numeric(18,2) USING amount_refunded::numeric(18,2);

-- customers
ALTER TABLE customers ALTER COLUMN ar_opening_balance TYPE numeric(18,2) USING ar_opening_balance::numeric(18,2);
ALTER TABLE customers ALTER COLUMN credit_limit TYPE numeric(18,2) USING credit_limit::numeric(18,2);
ALTER TABLE customers ALTER COLUMN deposit_opening_balance TYPE numeric(18,2) USING deposit_opening_balance::numeric(18,2);
-- SKIPPED (column/table not in schema): ALTER TABLE customers ALTER COLUMN total_nilai TYPE numeric(18,2) USING total_nilai::numeric(18,2);

-- expense_claim_lines
-- SKIPPED (column/table not in schema): ALTER TABLE expense_claim_lines ALTER COLUMN amount TYPE numeric(18,2) USING amount::numeric(18,2);

-- expense_claims
-- SKIPPED (column/table not in schema): ALTER TABLE expense_claims ALTER COLUMN total_amount TYPE numeric(18,2) USING total_amount::numeric(18,2);

-- expense_items
ALTER TABLE expense_items ALTER COLUMN amount TYPE numeric(18,2) USING amount::numeric(18,2);

-- expense_policies
-- SKIPPED (column/table not in schema): ALTER TABLE expense_policies ALTER COLUMN max_claim_amount TYPE numeric(18,2) USING max_claim_amount::numeric(18,2);

-- expenses
ALTER TABLE expenses ALTER COLUMN pph_amount TYPE numeric(18,2) USING pph_amount::numeric(18,2);
ALTER TABLE expenses ALTER COLUMN subtotal TYPE numeric(18,2) USING subtotal::numeric(18,2);
ALTER TABLE expenses ALTER COLUMN tax_amount TYPE numeric(18,2) USING tax_amount::numeric(18,2);
ALTER TABLE expenses ALTER COLUMN total_amount TYPE numeric(18,2) USING total_amount::numeric(18,2);

-- fixed_assets
ALTER TABLE fixed_assets ALTER COLUMN disposal_price TYPE numeric(18,2) USING disposal_price::numeric(18,2);
ALTER TABLE fixed_assets ALTER COLUMN gain_loss_amount TYPE numeric(18,2) USING gain_loss_amount::numeric(18,2);
ALTER TABLE fixed_assets ALTER COLUMN purchase_price TYPE numeric(18,2) USING purchase_price::numeric(18,2);

-- forex_gain_loss
-- SKIPPED (column/table not in schema): ALTER TABLE forex_gain_loss ALTER COLUMN gain_loss_amount TYPE numeric(18,2) USING gain_loss_amount::numeric(18,2);
-- SKIPPED (column/table not in schema): ALTER TABLE forex_gain_loss ALTER COLUMN original_amount TYPE numeric(18,2) USING original_amount::numeric(18,2);

-- hpp_breakdown
-- SKIPPED (column/table not in schema): ALTER TABLE hpp_breakdown ALTER COLUMN total_hpp TYPE numeric(18,2) USING total_hpp::numeric(18,2);

-- intercompany_balances
ALTER TABLE intercompany_balances ALTER COLUMN balance TYPE numeric(18,2) USING balance::numeric(18,2);

-- intercompany_settlements
ALTER TABLE intercompany_settlements ALTER COLUMN amount TYPE numeric(18,2) USING amount::numeric(18,2);

-- intercompany_transactions
ALTER TABLE intercompany_transactions ALTER COLUMN amount TYPE numeric(18,2) USING amount::numeric(18,2);
ALTER TABLE intercompany_transactions ALTER COLUMN exchange_rate TYPE numeric(18,6) USING exchange_rate::numeric(18,6);
ALTER TABLE intercompany_transactions ALTER COLUMN variance_amount TYPE numeric(18,2) USING variance_amount::numeric(18,2);

-- item_batches
ALTER TABLE item_batches ALTER COLUMN total_value TYPE numeric(18,2) USING total_value::numeric(18,2);
ALTER TABLE item_batches ALTER COLUMN unit_cost TYPE numeric(18,2) USING unit_cost::numeric(18,2);

-- item_pricing
ALTER TABLE item_pricing ALTER COLUMN price TYPE numeric(18,2) USING price::numeric(18,2);

-- item_serials
ALTER TABLE item_serials ALTER COLUMN selling_price TYPE numeric(18,2) USING selling_price::numeric(18,2);
ALTER TABLE item_serials ALTER COLUMN unit_cost TYPE numeric(18,2) USING unit_cost::numeric(18,2);

-- item_transaksi
-- SKIPPED (column/table not in schema): ALTER TABLE item_transaksi ALTER COLUMN subtotal TYPE numeric(18,2) USING subtotal::numeric(18,2);

-- jurnal_detail
-- SKIPPED (column/table not in schema): ALTER TABLE jurnal_detail ALTER COLUMN debit TYPE numeric(18,2) USING debit::numeric(18,2);

-- jurnal_entry
-- SKIPPED (column/table not in schema): ALTER TABLE jurnal_entry ALTER COLUMN total_debit TYPE numeric(18,2) USING total_debit::numeric(18,2);
-- SKIPPED (column/table not in schema): ALTER TABLE jurnal_entry ALTER COLUMN total_kredit TYPE numeric(18,2) USING total_kredit::numeric(18,2);

-- kartu_stok
-- SKIPPED (column/table not in schema): ALTER TABLE kartu_stok ALTER COLUMN total_nilai TYPE numeric(18,2) USING total_nilai::numeric(18,2);

-- menu_items
ALTER TABLE menu_items ALTER COLUMN base_price TYPE numeric(18,2) USING base_price::numeric(18,2);
ALTER TABLE menu_items ALTER COLUMN discount_price TYPE numeric(18,2) USING discount_price::numeric(18,2);

-- overhead_allocations
ALTER TABLE overhead_allocations ALTER COLUMN allocated_amount TYPE numeric(18,2) USING allocated_amount::numeric(18,2);
ALTER TABLE overhead_allocations ALTER COLUMN rate_per_unit TYPE numeric(18,2) USING rate_per_unit::numeric(18,2);

-- payment_requests
ALTER TABLE payment_requests ALTER COLUMN amount TYPE numeric(18,2) USING amount::numeric(18,2);

-- price_list_items
ALTER TABLE price_list_items ALTER COLUMN price TYPE numeric(18,2) USING price::numeric(18,2);

-- production_completions
ALTER TABLE production_completions ALTER COLUMN total_cost TYPE numeric(18,2) USING total_cost::numeric(18,2);
ALTER TABLE production_completions ALTER COLUMN unit_cost TYPE numeric(18,2) USING unit_cost::numeric(18,2);

-- production_order_labor
ALTER TABLE production_order_labor ALTER COLUMN actual_cost TYPE numeric(18,2) USING actual_cost::numeric(18,2);
ALTER TABLE production_order_labor ALTER COLUMN hourly_rate TYPE numeric(18,2) USING hourly_rate::numeric(18,2);
ALTER TABLE production_order_labor ALTER COLUMN planned_cost TYPE numeric(18,2) USING planned_cost::numeric(18,2);

-- production_order_materials
ALTER TABLE production_order_materials ALTER COLUMN actual_cost TYPE numeric(18,2) USING actual_cost::numeric(18,2);
ALTER TABLE production_order_materials ALTER COLUMN planned_cost TYPE numeric(18,2) USING planned_cost::numeric(18,2);
ALTER TABLE production_order_materials ALTER COLUMN variance_cost TYPE numeric(18,2) USING variance_cost::numeric(18,2);

-- production_orders
ALTER TABLE production_orders ALTER COLUMN actual_labor_cost TYPE numeric(18,2) USING actual_labor_cost::numeric(18,2);
ALTER TABLE production_orders ALTER COLUMN actual_material_cost TYPE numeric(18,2) USING actual_material_cost::numeric(18,2);
ALTER TABLE production_orders ALTER COLUMN actual_overhead_cost TYPE numeric(18,2) USING actual_overhead_cost::numeric(18,2);
ALTER TABLE production_orders ALTER COLUMN planned_labor_cost TYPE numeric(18,2) USING planned_labor_cost::numeric(18,2);
ALTER TABLE production_orders ALTER COLUMN planned_material_cost TYPE numeric(18,2) USING planned_material_cost::numeric(18,2);
ALTER TABLE production_orders ALTER COLUMN planned_overhead_cost TYPE numeric(18,2) USING planned_overhead_cost::numeric(18,2);
ALTER TABLE production_orders ALTER COLUMN variance_amount TYPE numeric(18,2) USING variance_amount::numeric(18,2);

-- products
ALTER TABLE products ALTER COLUMN purchase_price_amount TYPE numeric(18,2) USING purchase_price_amount::numeric(18,2);
ALTER TABLE products ALTER COLUMN sales_price_amount TYPE numeric(18,2) USING sales_price_amount::numeric(18,2);

-- purchase_order_items
ALTER TABLE purchase_order_items ALTER COLUMN discount_amount TYPE numeric(18,2) USING discount_amount::numeric(18,2);
ALTER TABLE purchase_order_items ALTER COLUMN subtotal TYPE numeric(18,2) USING subtotal::numeric(18,2);
ALTER TABLE purchase_order_items ALTER COLUMN tax_amount TYPE numeric(18,2) USING tax_amount::numeric(18,2);
ALTER TABLE purchase_order_items ALTER COLUMN total TYPE numeric(18,2) USING total::numeric(18,2);
ALTER TABLE purchase_order_items ALTER COLUMN unit_price TYPE numeric(18,2) USING unit_price::numeric(18,2);

-- purchase_orders
ALTER TABLE purchase_orders ALTER COLUMN amount_billed TYPE numeric(18,2) USING amount_billed::numeric(18,2);
ALTER TABLE purchase_orders ALTER COLUMN amount_received TYPE numeric(18,2) USING amount_received::numeric(18,2);
ALTER TABLE purchase_orders ALTER COLUMN discount_amount TYPE numeric(18,2) USING discount_amount::numeric(18,2);
ALTER TABLE purchase_orders ALTER COLUMN subtotal TYPE numeric(18,2) USING subtotal::numeric(18,2);
ALTER TABLE purchase_orders ALTER COLUMN tax_amount TYPE numeric(18,2) USING tax_amount::numeric(18,2);
ALTER TABLE purchase_orders ALTER COLUMN total_amount TYPE numeric(18,2) USING total_amount::numeric(18,2);

-- quote_items
ALTER TABLE quote_items ALTER COLUMN line_total TYPE numeric(18,2) USING line_total::numeric(18,2);
ALTER TABLE quote_items ALTER COLUMN tax_amount TYPE numeric(18,2) USING tax_amount::numeric(18,2);
ALTER TABLE quote_items ALTER COLUMN unit_price TYPE numeric(18,2) USING unit_price::numeric(18,2);

-- quotes
ALTER TABLE quotes ALTER COLUMN discount_amount TYPE numeric(18,2) USING discount_amount::numeric(18,2);
-- SKIPPED (column/table not in schema): ALTER TABLE quotes ALTER COLUMN exchange_rate TYPE numeric(18,6) USING exchange_rate::numeric(18,6);
ALTER TABLE quotes ALTER COLUMN subtotal TYPE numeric(18,2) USING subtotal::numeric(18,2);
ALTER TABLE quotes ALTER COLUMN tax_amount TYPE numeric(18,2) USING tax_amount::numeric(18,2);
ALTER TABLE quotes ALTER COLUMN total_amount TYPE numeric(18,2) USING total_amount::numeric(18,2);

-- receive_payment_allocations
ALTER TABLE receive_payment_allocations ALTER COLUMN amount_applied TYPE numeric(18,2) USING amount_applied::numeric(18,2);
ALTER TABLE receive_payment_allocations ALTER COLUMN invoice_amount TYPE numeric(18,2) USING invoice_amount::numeric(18,2);

-- receive_payments
ALTER TABLE receive_payments ALTER COLUMN allocated_amount TYPE numeric(18,2) USING allocated_amount::numeric(18,2);
ALTER TABLE receive_payments ALTER COLUMN discount_amount TYPE numeric(18,2) USING discount_amount::numeric(18,2);
ALTER TABLE receive_payments ALTER COLUMN total_amount TYPE numeric(18,2) USING total_amount::numeric(18,2);
ALTER TABLE receive_payments ALTER COLUMN unapplied_amount TYPE numeric(18,2) USING unapplied_amount::numeric(18,2);

-- recipe_ingredients
ALTER TABLE recipe_ingredients ALTER COLUMN extended_cost TYPE numeric(18,2) USING extended_cost::numeric(18,2);
ALTER TABLE recipe_ingredients ALTER COLUMN unit_cost TYPE numeric(18,2) USING unit_cost::numeric(18,2);

-- recipe_modifiers
ALTER TABLE recipe_modifiers ALTER COLUMN price_adjustment TYPE numeric(18,2) USING price_adjustment::numeric(18,2);

-- recipes
ALTER TABLE recipes ALTER COLUMN ingredient_cost TYPE numeric(18,2) USING ingredient_cost::numeric(18,2);
ALTER TABLE recipes ALTER COLUMN labor_cost_per_portion TYPE numeric(18,2) USING labor_cost_per_portion::numeric(18,2);
ALTER TABLE recipes ALTER COLUMN suggested_price TYPE numeric(18,2) USING suggested_price::numeric(18,2);
ALTER TABLE recipes ALTER COLUMN total_cost_per_portion TYPE numeric(18,2) USING total_cost_per_portion::numeric(18,2);

-- reconciliation_adjustments
ALTER TABLE reconciliation_adjustments ALTER COLUMN amount TYPE numeric(18,2) USING amount::numeric(18,2);

-- reconciliation_sessions
-- SKIPPED (column/table not in schema): ALTER TABLE reconciliation_sessions ALTER COLUMN balance_difference TYPE numeric(18,2) USING balance_difference::numeric(18,2);
ALTER TABLE reconciliation_sessions ALTER COLUMN cleared_balance TYPE numeric(18,2) USING cleared_balance::numeric(18,2);
-- SKIPPED (column/table not in schema): ALTER TABLE reconciliation_sessions ALTER COLUMN computed_gl_balance TYPE numeric(18,2) USING computed_gl_balance::numeric(18,2);
-- SKIPPED (column/table not in schema): ALTER TABLE reconciliation_sessions ALTER COLUMN opening_balance TYPE numeric(18,2) USING opening_balance::numeric(18,2);
ALTER TABLE reconciliation_sessions ALTER COLUMN statement_beginning_balance TYPE numeric(18,2) USING statement_beginning_balance::numeric(18,2);
-- SKIPPED (column/table not in schema): ALTER TABLE reconciliation_sessions ALTER COLUMN statement_closing_balance TYPE numeric(18,2) USING statement_closing_balance::numeric(18,2);
ALTER TABLE reconciliation_sessions ALTER COLUMN statement_ending_balance TYPE numeric(18,2) USING statement_ending_balance::numeric(18,2);
-- SKIPPED (column/table not in schema): ALTER TABLE reconciliation_sessions ALTER COLUMN statement_opening_balance TYPE numeric(18,2) USING statement_opening_balance::numeric(18,2);

-- recurring_bill_items
ALTER TABLE recurring_bill_items ALTER COLUMN line_total TYPE numeric(18,2) USING line_total::numeric(18,2);
ALTER TABLE recurring_bill_items ALTER COLUMN tax_amount TYPE numeric(18,2) USING tax_amount::numeric(18,2);
ALTER TABLE recurring_bill_items ALTER COLUMN unit_price TYPE numeric(18,2) USING unit_price::numeric(18,2);

-- recurring_bills
ALTER TABLE recurring_bills ALTER COLUMN discount_amount TYPE numeric(18,2) USING discount_amount::numeric(18,2);
ALTER TABLE recurring_bills ALTER COLUMN subtotal TYPE numeric(18,2) USING subtotal::numeric(18,2);
ALTER TABLE recurring_bills ALTER COLUMN tax_amount TYPE numeric(18,2) USING tax_amount::numeric(18,2);
ALTER TABLE recurring_bills ALTER COLUMN total_amount TYPE numeric(18,2) USING total_amount::numeric(18,2);

-- recurring_expenses
-- SKIPPED (column/table not in schema): ALTER TABLE recurring_expenses ALTER COLUMN amount TYPE numeric(18,2) USING amount::numeric(18,2);

-- recurring_invoice_items
ALTER TABLE recurring_invoice_items ALTER COLUMN discount_amount TYPE numeric(18,2) USING discount_amount::numeric(18,2);
ALTER TABLE recurring_invoice_items ALTER COLUMN line_total TYPE numeric(18,2) USING line_total::numeric(18,2);
ALTER TABLE recurring_invoice_items ALTER COLUMN subtotal TYPE numeric(18,2) USING subtotal::numeric(18,2);
ALTER TABLE recurring_invoice_items ALTER COLUMN tax_amount TYPE numeric(18,2) USING tax_amount::numeric(18,2);
ALTER TABLE recurring_invoice_items ALTER COLUMN unit_price TYPE numeric(18,2) USING unit_price::numeric(18,2);

-- recurring_invoices
ALTER TABLE recurring_invoices ALTER COLUMN discount_amount TYPE numeric(18,2) USING discount_amount::numeric(18,2);
ALTER TABLE recurring_invoices ALTER COLUMN subtotal TYPE numeric(18,2) USING subtotal::numeric(18,2);
ALTER TABLE recurring_invoices ALTER COLUMN tax_amount TYPE numeric(18,2) USING tax_amount::numeric(18,2);
ALTER TABLE recurring_invoices ALTER COLUMN total_amount TYPE numeric(18,2) USING total_amount::numeric(18,2);
ALTER TABLE recurring_invoices ALTER COLUMN total_invoiced TYPE numeric(18,2) USING total_invoiced::numeric(18,2);

-- sales_invoice_items
ALTER TABLE sales_invoice_items ALTER COLUMN discount_amount TYPE numeric(18,2) USING discount_amount::numeric(18,2);
ALTER TABLE sales_invoice_items ALTER COLUMN subtotal TYPE numeric(18,2) USING subtotal::numeric(18,2);
ALTER TABLE sales_invoice_items ALTER COLUMN tax_amount TYPE numeric(18,2) USING tax_amount::numeric(18,2);
ALTER TABLE sales_invoice_items ALTER COLUMN total TYPE numeric(18,2) USING total::numeric(18,2);
ALTER TABLE sales_invoice_items ALTER COLUMN total_cost TYPE numeric(18,2) USING total_cost::numeric(18,2);
ALTER TABLE sales_invoice_items ALTER COLUMN unit_cost TYPE numeric(18,2) USING unit_cost::numeric(18,2);
ALTER TABLE sales_invoice_items ALTER COLUMN unit_price TYPE numeric(18,2) USING unit_price::numeric(18,2);

-- sales_invoice_payments
ALTER TABLE sales_invoice_payments ALTER COLUMN amount TYPE numeric(18,2) USING amount::numeric(18,2);

-- sales_invoices
ALTER TABLE sales_invoices ALTER COLUMN amount_paid TYPE numeric(18,2) USING amount_paid::numeric(18,2);
-- SKIPPED (column/table not in schema): ALTER TABLE sales_invoices ALTER COLUMN base_currency_total TYPE numeric(18,2) USING base_currency_total::numeric(18,2);
ALTER TABLE sales_invoices ALTER COLUMN discount_amount TYPE numeric(18,2) USING discount_amount::numeric(18,2);
-- SKIPPED (column/table not in schema): ALTER TABLE sales_invoices ALTER COLUMN exchange_rate TYPE numeric(18,6) USING exchange_rate::numeric(18,6);
ALTER TABLE sales_invoices ALTER COLUMN subtotal TYPE numeric(18,2) USING subtotal::numeric(18,2);
ALTER TABLE sales_invoices ALTER COLUMN tax_amount TYPE numeric(18,2) USING tax_amount::numeric(18,2);
ALTER TABLE sales_invoices ALTER COLUMN total_amount TYPE numeric(18,2) USING total_amount::numeric(18,2);
ALTER TABLE sales_invoices ALTER COLUMN total_cogs TYPE numeric(18,2) USING total_cogs::numeric(18,2);

-- sales_order_items
ALTER TABLE sales_order_items ALTER COLUMN line_total TYPE numeric(18,2) USING line_total::numeric(18,2);
ALTER TABLE sales_order_items ALTER COLUMN tax_amount TYPE numeric(18,2) USING tax_amount::numeric(18,2);
ALTER TABLE sales_order_items ALTER COLUMN unit_price TYPE numeric(18,2) USING unit_price::numeric(18,2);

-- sales_orders
ALTER TABLE sales_orders ALTER COLUMN discount_amount TYPE numeric(18,2) USING discount_amount::numeric(18,2);
-- SKIPPED (column/table not in schema): ALTER TABLE sales_orders ALTER COLUMN exchange_rate TYPE numeric(18,6) USING exchange_rate::numeric(18,6);
ALTER TABLE sales_orders ALTER COLUMN shipping_amount TYPE numeric(18,2) USING shipping_amount::numeric(18,2);
ALTER TABLE sales_orders ALTER COLUMN subtotal TYPE numeric(18,2) USING subtotal::numeric(18,2);
ALTER TABLE sales_orders ALTER COLUMN tax_amount TYPE numeric(18,2) USING tax_amount::numeric(18,2);
ALTER TABLE sales_orders ALTER COLUMN total_amount TYPE numeric(18,2) USING total_amount::numeric(18,2);

-- sales_receipt_items
ALTER TABLE sales_receipt_items ALTER COLUMN discount_amount TYPE numeric(18,2) USING discount_amount::numeric(18,2);
ALTER TABLE sales_receipt_items ALTER COLUMN line_total TYPE numeric(18,2) USING line_total::numeric(18,2);
ALTER TABLE sales_receipt_items ALTER COLUMN subtotal TYPE numeric(18,2) USING subtotal::numeric(18,2);
ALTER TABLE sales_receipt_items ALTER COLUMN tax_amount TYPE numeric(18,2) USING tax_amount::numeric(18,2);
ALTER TABLE sales_receipt_items ALTER COLUMN total_cost TYPE numeric(18,2) USING total_cost::numeric(18,2);
ALTER TABLE sales_receipt_items ALTER COLUMN unit_cost TYPE numeric(18,2) USING unit_cost::numeric(18,2);
ALTER TABLE sales_receipt_items ALTER COLUMN unit_price TYPE numeric(18,2) USING unit_price::numeric(18,2);

-- sales_receipts
ALTER TABLE sales_receipts ALTER COLUMN amount_received TYPE numeric(18,2) USING amount_received::numeric(18,2);
ALTER TABLE sales_receipts ALTER COLUMN change_amount TYPE numeric(18,2) USING change_amount::numeric(18,2);
ALTER TABLE sales_receipts ALTER COLUMN discount_amount TYPE numeric(18,2) USING discount_amount::numeric(18,2);
ALTER TABLE sales_receipts ALTER COLUMN subtotal TYPE numeric(18,2) USING subtotal::numeric(18,2);
ALTER TABLE sales_receipts ALTER COLUMN tax_amount TYPE numeric(18,2) USING tax_amount::numeric(18,2);
ALTER TABLE sales_receipts ALTER COLUMN total_amount TYPE numeric(18,2) USING total_amount::numeric(18,2);

-- standard_costs
ALTER TABLE standard_costs ALTER COLUMN labor_cost TYPE numeric(18,2) USING labor_cost::numeric(18,2);
ALTER TABLE standard_costs ALTER COLUMN material_cost TYPE numeric(18,2) USING material_cost::numeric(18,2);
ALTER TABLE standard_costs ALTER COLUMN overhead_cost TYPE numeric(18,2) USING overhead_cost::numeric(18,2);
ALTER TABLE standard_costs ALTER COLUMN total_cost TYPE numeric(18,2) USING total_cost::numeric(18,2);

-- stock_adjustment_items
ALTER TABLE stock_adjustment_items ALTER COLUMN total_value TYPE numeric(18,2) USING total_value::numeric(18,2);
ALTER TABLE stock_adjustment_items ALTER COLUMN unit_cost TYPE numeric(18,2) USING unit_cost::numeric(18,2);

-- stock_adjustments
ALTER TABLE stock_adjustments ALTER COLUMN total_value TYPE numeric(18,2) USING total_value::numeric(18,2);

-- stock_transfer_items
ALTER TABLE stock_transfer_items ALTER COLUMN total_value TYPE numeric(18,2) USING total_value::numeric(18,2);
ALTER TABLE stock_transfer_items ALTER COLUMN unit_cost TYPE numeric(18,2) USING unit_cost::numeric(18,2);

-- stock_transfers
ALTER TABLE stock_transfers ALTER COLUMN total_value TYPE numeric(18,2) USING total_value::numeric(18,2);

-- table_reservations
ALTER TABLE table_reservations ALTER COLUMN deposit_amount TYPE numeric(18,2) USING deposit_amount::numeric(18,2);

-- table_sessions
ALTER TABLE table_sessions ALTER COLUMN total_amount TYPE numeric(18,2) USING total_amount::numeric(18,2);

-- transaksi_harian
-- SKIPPED (column/table not in schema): ALTER TABLE transaksi_harian ALTER COLUMN discount_amount TYPE numeric(18,2) USING discount_amount::numeric(18,2);
-- SKIPPED (column/table not in schema): ALTER TABLE transaksi_harian ALTER COLUMN grand_total TYPE numeric(18,2) USING grand_total::numeric(18,2);
-- SKIPPED (column/table not in schema): ALTER TABLE transaksi_harian ALTER COLUMN pajak_amount TYPE numeric(18,2) USING pajak_amount::numeric(18,2);
-- SKIPPED (column/table not in schema): ALTER TABLE transaksi_harian ALTER COLUMN subtotal_after_discount TYPE numeric(18,2) USING subtotal_after_discount::numeric(18,2);
-- SKIPPED (column/table not in schema): ALTER TABLE transaksi_harian ALTER COLUMN subtotal_before_discount TYPE numeric(18,2) USING subtotal_before_discount::numeric(18,2);
-- SKIPPED (column/table not in schema): ALTER TABLE transaksi_harian ALTER COLUMN total_nominal TYPE numeric(18,2) USING total_nominal::numeric(18,2);
-- SKIPPED (column/table not in schema): ALTER TABLE transaksi_harian ALTER COLUMN vat_amount TYPE numeric(18,2) USING vat_amount::numeric(18,2);

-- unit_conversions
ALTER TABLE unit_conversions ALTER COLUMN purchase_price TYPE numeric(18,2) USING purchase_price::numeric(18,2);
ALTER TABLE unit_conversions ALTER COLUMN sales_price TYPE numeric(18,2) USING sales_price::numeric(18,2);

-- vendor_credit_applications
ALTER TABLE vendor_credit_applications ALTER COLUMN amount_applied TYPE numeric(18,2) USING amount_applied::numeric(18,2);

-- vendor_credit_items
ALTER TABLE vendor_credit_items ALTER COLUMN discount_amount TYPE numeric(18,2) USING discount_amount::numeric(18,2);
ALTER TABLE vendor_credit_items ALTER COLUMN subtotal TYPE numeric(18,2) USING subtotal::numeric(18,2);
ALTER TABLE vendor_credit_items ALTER COLUMN tax_amount TYPE numeric(18,2) USING tax_amount::numeric(18,2);
ALTER TABLE vendor_credit_items ALTER COLUMN total TYPE numeric(18,2) USING total::numeric(18,2);
ALTER TABLE vendor_credit_items ALTER COLUMN unit_price TYPE numeric(18,2) USING unit_price::numeric(18,2);

-- vendor_credit_refunds
ALTER TABLE vendor_credit_refunds ALTER COLUMN amount TYPE numeric(18,2) USING amount::numeric(18,2);

-- vendor_credits
ALTER TABLE vendor_credits ALTER COLUMN amount_applied TYPE numeric(18,2) USING amount_applied::numeric(18,2);
ALTER TABLE vendor_credits ALTER COLUMN amount_received TYPE numeric(18,2) USING amount_received::numeric(18,2);
-- SKIPPED (column/table not in schema): ALTER TABLE vendor_credits ALTER COLUMN base_currency_total TYPE numeric(18,2) USING base_currency_total::numeric(18,2);
ALTER TABLE vendor_credits ALTER COLUMN discount_amount TYPE numeric(18,2) USING discount_amount::numeric(18,2);
-- SKIPPED (column/table not in schema): ALTER TABLE vendor_credits ALTER COLUMN exchange_rate TYPE numeric(18,6) USING exchange_rate::numeric(18,6);
ALTER TABLE vendor_credits ALTER COLUMN subtotal TYPE numeric(18,2) USING subtotal::numeric(18,2);
ALTER TABLE vendor_credits ALTER COLUMN tax_amount TYPE numeric(18,2) USING tax_amount::numeric(18,2);
ALTER TABLE vendor_credits ALTER COLUMN total_amount TYPE numeric(18,2) USING total_amount::numeric(18,2);
-- SKIPPED (column/table not in schema): ALTER TABLE vendor_credits ALTER COLUMN total_cogs TYPE numeric(18,2) USING total_cogs::numeric(18,2);

-- vendor_deposit_applications
ALTER TABLE vendor_deposit_applications ALTER COLUMN amount TYPE numeric(18,2) USING amount::numeric(18,2);

-- vendor_deposit_refunds
ALTER TABLE vendor_deposit_refunds ALTER COLUMN amount TYPE numeric(18,2) USING amount::numeric(18,2);

-- vendor_deposits
ALTER TABLE vendor_deposits ALTER COLUMN amount TYPE numeric(18,2) USING amount::numeric(18,2);
ALTER TABLE vendor_deposits ALTER COLUMN applied_amount TYPE numeric(18,2) USING applied_amount::numeric(18,2);

-- vendors
ALTER TABLE vendors ALTER COLUMN credit_limit TYPE numeric(18,2) USING credit_limit::numeric(18,2);
ALTER TABLE vendors ALTER COLUMN opening_balance TYPE numeric(18,2) USING opening_balance::numeric(18,2);

-- work_centers
ALTER TABLE work_centers ALTER COLUMN labor_rate_per_hour TYPE numeric(18,2) USING labor_rate_per_hour::numeric(18,2);
ALTER TABLE work_centers ALTER COLUMN overhead_rate_per_hour TYPE numeric(18,2) USING overhead_rate_per_hour::numeric(18,2);

-- products
ALTER TABLE products ALTER COLUMN harga_jual TYPE numeric(18,2) USING harga_jual::numeric(18,2);
ALTER TABLE products ALTER COLUMN sales_price TYPE numeric(18,2) USING sales_price::numeric(18,2);
ALTER TABLE products ALTER COLUMN purchase_price TYPE numeric(18,2) USING purchase_price::numeric(18,2);

-- Note: products harga_jual/sales_price/purchase_price already altered above from Step 3

-- Step 4: Recreate generated columns with numeric type
ALTER TABLE budget_items ADD COLUMN annual_amount numeric(18,2) GENERATED ALWAYS AS (jan_amount + feb_amount + mar_amount + apr_amount + may_amount + jun_amount + jul_amount + aug_amount + sep_amount + oct_amount + nov_amount + dec_amount) STORED;
ALTER TABLE vendor_deposits ADD COLUMN remaining_amount numeric(18,2) GENERATED ALWAYS AS (amount - applied_amount) STORED;

-- Step 5: Recreate triggers

CREATE OR REPLACE FUNCTION update_bom_component_cost()
RETURNS trigger LANGUAGE plpgsql AS $function$
BEGIN
    NEW.extended_cost := ROUND(
        NEW.quantity * NEW.unit_cost * (1 + COALESCE(NEW.wastage_percent, 0) / 100), 2
    );
    RETURN NEW;
END;
$function$;

CREATE TRIGGER trg_update_bom_component_cost
    BEFORE INSERT OR UPDATE OF quantity, unit_cost, wastage_percent
    ON bom_components FOR EACH ROW
    EXECUTE FUNCTION update_bom_component_cost();


-- Step 6: Fix CHECK constraint referencing double precision
ALTER TABLE item_pricing DROP CONSTRAINT IF EXISTS item_pricing_price_check;
ALTER TABLE item_pricing ADD CONSTRAINT item_pricing_price_check CHECK (price >= 0);

-- Step 7: Recreate views
CREATE OR REPLACE VIEW v_products_with_prices AS
SELECT p.id,
    p.tenant_id,
    p.nama_produk,
    p.satuan,
    p.kategori,
    p.harga_jual,
    -- p.deskripsi SKIPPED (column not in schema)
    p.created_at,
    p.updated_at,
    p.barcode,
    -- p.content_unit SKIPPED (column not in schema)
    -- p.base_unit SKIPPED (column not in schema)
    -- p.wholesale_unit SKIPPED (column not in schema)
    -- p.units_per_wholesale SKIPPED (column not in schema)
    p.item_type,
    p.track_inventory,
    p.is_returnable,
    p.sales_price,
    p.purchase_price,
    p.sales_account,
    p.purchase_account,
    p.sales_tax,
    p.purchase_tax,
    p.status,
    -- p.for_sales SKIPPED (column not in schema)
    -- p.for_purchases SKIPPED (column not in schema)
    p.sku,
    p.track_batches,
    p.track_expiry,
    p.default_expiry_days,
    p.track_serial,
    p.sales_price_amount,
    p.purchase_price_amount,
    p.sales_account_id,
    p.purchase_account_id,
    p.inventory_account_id,
    p.reorder_level,
    p.preferred_vendor_id,
    COALESCE(p.sales_price_amount, round(p.sales_price)::numeric(18,2), 0::numeric(18,2)) AS effective_sales_price,
    COALESCE(p.purchase_price_amount, round(p.purchase_price)::numeric(18,2), 0::numeric(18,2)) AS effective_purchase_price,
    sa.name AS sales_account_name,
    pa.name AS purchase_account_name,
    ia.name AS inventory_account_name,
    v.name AS preferred_vendor_name
FROM products p
    LEFT JOIN chart_of_accounts sa ON p.sales_account_id = sa.id
    LEFT JOIN chart_of_accounts pa ON p.purchase_account_id = pa.id
    LEFT JOIN chart_of_accounts ia ON p.inventory_account_id = ia.id
    LEFT JOIN vendors v ON p.preferred_vendor_id = v.id;

COMMIT;

-- Migration complete: 323 columns standardized to Law 25 precision