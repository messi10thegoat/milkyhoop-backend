# MilkyHoop ERP - Extended Field Mapping Audit Report
## Modules 7-16 (Quotes, Orders, Inventory, Credits, Banking)

**Audit Date:** 2026-01-16
**Auditor:** Claude (AI)
**Frontend:** `/root/milkyhoop/frontend/web/`
**Backend:** `/root/milkyhoop-dev/backend/`

---

## Executive Summary

| Module | Backend Status | Frontend Status | Gap Severity |
|--------|---------------|-----------------|--------------|
| 7. Quotes | ✅ COMPLETE | ❌ NOT FOUND | **CRITICAL** |
| 8. Sales Orders | ✅ COMPLETE | ❌ NOT FOUND | **CRITICAL** |
| 9. Purchase Orders | ✅ COMPLETE | ❌ NOT FOUND | **CRITICAL** |
| 10. Production Orders | ✅ COMPLETE | ❌ NOT FOUND | **CRITICAL** |
| 11. Stock Adjustments | ✅ COMPLETE | ✅ FOUND (Modal) | Minor |
| 12. Stock Transfers | ✅ COMPLETE | ❌ NOT FOUND | **MAJOR** |
| 13. Credit Notes | ✅ COMPLETE | ❌ NOT FOUND | **CRITICAL** |
| 14. Vendor Credits | ✅ COMPLETE | ❌ NOT FOUND | **CRITICAL** |
| 15. Bank Accounts & Transfers | ✅ COMPLETE | ⚠️ PARTIAL | Major |
| 16. Customer Deposits | ✅ COMPLETE | ⚠️ PARTIAL | Major |

**Legend:** ✅ Complete | ⚠️ Partial | ❌ Not Found

---

## Module 7: Quotes (Penawaran)

```yaml
module: quotes
status: NOT_FOUND

frontend:
  component_path: null
  lines: 0
  pattern: null
  module_definition:
    location: /src/components/app/ChatPanel/actionMenuConstants.ts:171
    module_id: penawaran
    category: penjualan

backend:
  router_path: /api_gateway/app/routers/quotes.py
  lines: 1200+
  schema_path: /api_gateway/app/schemas/quotes.py

api_endpoints:
  list: GET /api/quotes
  detail: GET /api/quotes/{id}
  create: POST /api/quotes
  update: PATCH /api/quotes/{id}
  delete: DELETE /api/quotes/{id}
  send: POST /api/quotes/{id}/send
  decline: POST /api/quotes/{id}/decline
  convert_to_invoice: POST /api/quotes/{id}/to-invoice
  convert_to_order: POST /api/quotes/{id}/to-order
  duplicate: POST /api/quotes/{id}/duplicate
  expiring: GET /api/quotes/expiring
  summary: GET /api/quotes/summary

form_fields:
  header:
    - frontend_name: null
      backend_name: customer_id
      type: uuid
      required: true
    - frontend_name: null
      backend_name: quote_number
      type: string
      required: false (auto-generated)
    - frontend_name: null
      backend_name: quote_date
      type: date
      required: true
    - frontend_name: null
      backend_name: expiry_date
      type: date
      required: false
    - frontend_name: null
      backend_name: subject
      type: string
      required: false
    - frontend_name: null
      backend_name: notes
      type: string
      required: false
    - frontend_name: null
      backend_name: terms_conditions
      type: string
      required: false
    - frontend_name: null
      backend_name: discount_type
      type: enum (percentage|fixed)
      required: false
    - frontend_name: null
      backend_name: discount_value
      type: number
      required: false

  line_items:
    - frontend_name: null
      backend_name: product_id
      type: uuid
      required: true
    - frontend_name: null
      backend_name: description
      type: string
      required: false
    - frontend_name: null
      backend_name: quantity
      type: decimal
      required: true
    - frontend_name: null
      backend_name: unit_price
      type: integer
      required: true
    - frontend_name: null
      backend_name: discount_percent
      type: decimal
      required: false
    - frontend_name: null
      backend_name: tax_rate
      type: decimal
      required: false
    - frontend_name: null
      backend_name: tax_amount
      type: integer
      required: false (calculated)
    - frontend_name: null
      backend_name: line_total
      type: integer
      required: false (calculated)

status_flow:
  - from: draft
    to: [sent]
    action: send
    endpoint: POST /api/quotes/{id}/send
  - from: sent
    to: [viewed, accepted, declined, expired]
    action: customer_action
  - from: accepted
    to: [converted]
    action: convert
    endpoint: POST /api/quotes/{id}/to-invoice OR /to-order

button_actions:
  - label: "Kirim Penawaran"
    action: send
    endpoint: POST /api/quotes/{id}/send
    condition: status == 'draft'
  - label: "Terima"
    action: accept
    endpoint: POST /api/quotes/{id}/accept
    condition: status == 'sent'
  - label: "Tolak"
    action: decline
    endpoint: POST /api/quotes/{id}/decline
    condition: status == 'sent'
  - label: "Buat Faktur"
    action: convert_to_invoice
    endpoint: POST /api/quotes/{id}/to-invoice
    condition: status == 'accepted'
  - label: "Buat Pesanan"
    action: convert_to_order
    endpoint: POST /api/quotes/{id}/to-order
    condition: status == 'accepted'
  - label: "Duplikat"
    action: duplicate
    endpoint: POST /api/quotes/{id}/duplicate
    condition: always

gaps_found:
  - severity: critical
    description: "NO frontend component exists for Quotes module"
  - severity: major
    description: "Module defined in actionMenuConstants.ts but not implemented"

recommendations:
  - "Create QuotesPanel.tsx following KasBankPanel pattern"
  - "Create QuoteForm.tsx for quote creation/editing"
  - "Implement quote list view with status filters"
  - "Add expiry date countdown/alerts"
  - "Support conversion to Invoice/Sales Order"
```

---

## Module 8: Sales Orders (Pesanan Penjualan)

```yaml
module: sales_orders
status: NOT_FOUND

frontend:
  component_path: null
  lines: 0
  pattern: null
  module_definition:
    location: /src/components/app/ChatPanel/actionMenuConstants.ts:172
    module_id: pesanan_penjualan
    category: penjualan

backend:
  router_path: /api_gateway/app/routers/sales_orders.py
  lines: 1200+
  schema_path: /api_gateway/app/schemas/sales_orders.py

api_endpoints:
  list: GET /api/sales-orders
  pending: GET /api/sales-orders/pending
  summary: GET /api/sales-orders/summary
  detail: GET /api/sales-orders/{id}
  create: POST /api/sales-orders
  update: PATCH /api/sales-orders/{id}
  delete: DELETE /api/sales-orders/{id}
  confirm: POST /api/sales-orders/{id}/confirm
  ship: POST /api/sales-orders/{id}/ship
  convert_to_invoice: POST /api/sales-orders/{id}/to-invoice
  cancel: POST /api/sales-orders/{id}/cancel

form_fields:
  header:
    - frontend_name: null
      backend_name: customer_id
      type: uuid
      required: true
    - frontend_name: null
      backend_name: order_number
      type: string
      required: false (auto-generated)
    - frontend_name: null
      backend_name: order_date
      type: date
      required: true
    - frontend_name: null
      backend_name: expected_ship_date
      type: date
      required: false
    - frontend_name: null
      backend_name: shipping_address
      type: string
      required: false
    - frontend_name: null
      backend_name: discount_amount
      type: integer
      required: false
    - frontend_name: null
      backend_name: shipping_amount
      type: integer
      required: false
    - frontend_name: null
      backend_name: notes
      type: string
      required: false

  line_items:
    - frontend_name: null
      backend_name: product_id
      type: uuid
      required: true
    - frontend_name: null
      backend_name: quantity
      type: decimal
      required: true
    - frontend_name: null
      backend_name: quantity_shipped
      type: decimal
      required: false (tracked)
    - frontend_name: null
      backend_name: quantity_invoiced
      type: decimal
      required: false (tracked)
    - frontend_name: null
      backend_name: unit_price
      type: integer
      required: true
    - frontend_name: null
      backend_name: discount_percent
      type: decimal
      required: false
    - frontend_name: null
      backend_name: tax_rate
      type: decimal
      required: false
    - frontend_name: null
      backend_name: tax_amount
      type: integer
      required: false (calculated)
    - frontend_name: null
      backend_name: line_total
      type: integer
      required: false (calculated)

  shipment_tracking:
    - frontend_name: null
      backend_name: shipment_number
      type: string
    - frontend_name: null
      backend_name: shipment_date
      type: date
    - frontend_name: null
      backend_name: carrier
      type: string
    - frontend_name: null
      backend_name: tracking_number
      type: string
    - frontend_name: null
      backend_name: items_shipped
      type: array

status_flow:
  - from: draft
    to: [confirmed, cancelled]
    action: confirm|cancel
  - from: confirmed
    to: [partial_shipped, cancelled]
    action: ship|cancel
  - from: partial_shipped
    to: [shipped]
    action: ship_remaining
  - from: shipped
    to: [partial_invoiced, invoiced]
    action: create_invoice
  - from: partial_invoiced
    to: [invoiced]
    action: invoice_remaining
  - from: invoiced
    to: [completed]
    action: complete

button_actions:
  - label: "Konfirmasi"
    action: confirm
    endpoint: POST /api/sales-orders/{id}/confirm
    condition: status == 'draft'
  - label: "Kirim Barang"
    action: ship
    endpoint: POST /api/sales-orders/{id}/ship
    condition: status in ['confirmed', 'partial_shipped']
  - label: "Buat Faktur"
    action: convert_to_invoice
    endpoint: POST /api/sales-orders/{id}/to-invoice
    condition: status in ['shipped', 'partial_invoiced']
  - label: "Batalkan"
    action: cancel
    endpoint: POST /api/sales-orders/{id}/cancel
    condition: status not in ['invoiced', 'completed', 'cancelled']

gaps_found:
  - severity: critical
    description: "NO frontend component exists for Sales Orders"
  - severity: major
    description: "Shipment tracking UI not available"
  - severity: major
    description: "Partial shipment handling not implemented"

recommendations:
  - "Create SalesOrderPanel.tsx with list/detail views"
  - "Create SalesOrderForm.tsx for order creation"
  - "Create ShipmentDialog.tsx for recording shipments"
  - "Implement partial shipment with item selection"
  - "Add shipment timeline/tracking view"
```

---

## Module 9: Purchase Orders (Pesanan Pembelian)

```yaml
module: purchase_orders
status: NOT_FOUND

frontend:
  component_path: null
  lines: 0
  pattern: null
  module_definition:
    location: /src/components/app/ChatPanel/actionMenuConstants.ts:181
    module_id: pesanan_pembelian
    category: pembelian
  type_file:
    path: /src/types/purchase.ts
    lines: 80

backend:
  router_path: /api_gateway/app/routers/purchase_orders.py
  lines: 1500+
  schema_path: /api_gateway/app/schemas/purchase_orders.py

api_endpoints:
  list: GET /api/purchase-orders
  pending: GET /api/purchase-orders/pending
  summary: GET /api/purchase-orders/summary
  detail: GET /api/purchase-orders/{id}
  create: POST /api/purchase-orders
  update: PATCH /api/purchase-orders/{id}
  delete: DELETE /api/purchase-orders/{id}
  send: POST /api/purchase-orders/{id}/send
  receive: POST /api/purchase-orders/{id}/receive
  convert_to_bill: POST /api/purchase-orders/{id}/to-bill
  cancel: POST /api/purchase-orders/{id}/cancel
  close: POST /api/purchase-orders/{id}/close
  vendor_orders: GET /api/vendors/{id}/purchase-orders

form_fields:
  header:
    - frontend_name: null
      backend_name: vendor_id
      type: uuid
      required: true
    - frontend_name: null
      backend_name: po_number
      type: string
      required: false (auto-generated)
    - frontend_name: null
      backend_name: po_date
      type: date
      required: true
    - frontend_name: null
      backend_name: expected_date
      type: date
      required: false
    - frontend_name: null
      backend_name: shipping_address
      type: string
      required: false
    - frontend_name: null
      backend_name: reference
      type: string
      required: false
    - frontend_name: null
      backend_name: notes
      type: string
      required: false

  line_items:
    - frontend_name: null
      backend_name: product_id
      type: uuid
      required: true
    - frontend_name: null
      backend_name: quantity
      type: decimal
      required: true
    - frontend_name: null
      backend_name: quantity_received
      type: decimal
      required: false (tracked)
    - frontend_name: null
      backend_name: quantity_billed
      type: decimal
      required: false (tracked)
    - frontend_name: null
      backend_name: unit_price
      type: integer
      required: true
    - frontend_name: null
      backend_name: discount_percent
      type: decimal
      required: false
    - frontend_name: null
      backend_name: discount_amount
      type: integer
      required: false
    - frontend_name: null
      backend_name: tax_rate
      type: decimal
      required: false
    - frontend_name: null
      backend_name: tax_amount
      type: integer
      required: false (calculated)
    - frontend_name: null
      backend_name: total
      type: integer
      required: false (calculated)

status_flow:
  - from: draft
    to: [sent, cancelled]
    action: send|cancel
  - from: sent
    to: [partial_received, cancelled]
    action: receive|cancel
  - from: partial_received
    to: [received]
    action: receive_remaining
  - from: received
    to: [partial_billed, billed]
    action: create_bill
  - from: partial_billed
    to: [billed]
    action: bill_remaining
  - from: billed
    to: [closed]
    action: close

button_actions:
  - label: "Kirim ke Vendor"
    action: send
    endpoint: POST /api/purchase-orders/{id}/send
    condition: status == 'draft'
  - label: "Terima Barang"
    action: receive
    endpoint: POST /api/purchase-orders/{id}/receive
    condition: status in ['sent', 'partial_received']
  - label: "Buat Tagihan"
    action: convert_to_bill
    endpoint: POST /api/purchase-orders/{id}/to-bill
    condition: status in ['received', 'partial_billed']
  - label: "Batalkan"
    action: cancel
    endpoint: POST /api/purchase-orders/{id}/cancel
    condition: status in ['draft', 'sent']

gaps_found:
  - severity: critical
    description: "NO frontend component exists for Purchase Orders"
  - severity: major
    description: "Goods receipt (penerimaan barang) UI not available"
  - severity: major
    description: "Partial receive/bill handling not implemented"

recommendations:
  - "Create PurchaseOrderPanel.tsx with list/detail views"
  - "Create PurchaseOrderForm.tsx for PO creation"
  - "Create GoodsReceiptDialog.tsx for recording receipts"
  - "Implement 3-way matching (PO vs Receipt vs Bill)"
  - "Add vendor order history view"
```

---

## Module 10: Production Orders (Perintah Produksi)

```yaml
module: production_orders
status: NOT_FOUND

frontend:
  component_path: null
  lines: 0
  pattern: null
  module_definition: NOT FOUND IN actionMenuConstants.ts

backend:
  router_path: /api_gateway/app/routers/production.py
  lines: 1200+
  schema_path: /api_gateway/app/schemas/production.py

api_endpoints:
  health: GET /api/production/health
  list: GET /api/production
  list_alias: GET /api/production/orders
  detail: GET /api/production/{order_id}
  create: POST /api/production
  update: PATCH /api/production/{order_id}
  delete: DELETE /api/production/{order_id}
  issue_materials: POST /api/production/{order_id}/issue
  record_labor: POST /api/production/{order_id}/labor
  complete: POST /api/production/{order_id}/complete
  cancel: POST /api/production/{order_id}/cancel
  cost_analysis: GET /api/production/{order_id}/costs
  schedule: GET /api/production/schedule
  capacity: GET /api/production/capacity

form_fields:
  header:
    - frontend_name: null
      backend_name: product_id
      type: uuid
      required: true
      description: "Finished good to produce"
    - frontend_name: null
      backend_name: bom_id
      type: uuid
      required: false
      description: "Bill of Materials"
    - frontend_name: null
      backend_name: order_number
      type: string
      required: false (auto-generated)
    - frontend_name: null
      backend_name: order_date
      type: date
      required: true
    - frontend_name: null
      backend_name: planned_quantity
      type: decimal
      required: true
    - frontend_name: null
      backend_name: completed_quantity
      type: decimal
      required: false (tracked)
    - frontend_name: null
      backend_name: planned_start_date
      type: date
      required: true
    - frontend_name: null
      backend_name: planned_end_date
      type: date
      required: true
    - frontend_name: null
      backend_name: actual_start_date
      type: date
      required: false
    - frontend_name: null
      backend_name: actual_end_date
      type: date
      required: false
    - frontend_name: null
      backend_name: priority
      type: integer (1-10)
      required: false

  materials:
    - frontend_name: null
      backend_name: component_id
      type: uuid
      required: true
    - frontend_name: null
      backend_name: required_quantity
      type: decimal
      required: true
    - frontend_name: null
      backend_name: issued_quantity
      type: decimal
      required: false (tracked)
    - frontend_name: null
      backend_name: unit
      type: string
      required: false
    - frontend_name: null
      backend_name: unit_cost
      type: integer
      required: false

  labor:
    - frontend_name: null
      backend_name: operation
      type: string
      required: false
    - frontend_name: null
      backend_name: work_center
      type: string
      required: false
    - frontend_name: null
      backend_name: planned_hours
      type: decimal
      required: false
    - frontend_name: null
      backend_name: actual_hours
      type: decimal
      required: false
    - frontend_name: null
      backend_name: labor_rate
      type: decimal
      required: false

  costs:
    - frontend_name: null
      backend_name: material_cost
      type: integer
      required: false (calculated)
    - frontend_name: null
      backend_name: labor_cost
      type: integer
      required: false (calculated)
    - frontend_name: null
      backend_name: overhead_cost
      type: integer
      required: false
    - frontend_name: null
      backend_name: total_cost
      type: integer
      required: false (calculated)
    - frontend_name: null
      backend_name: unit_cost
      type: integer
      required: false (calculated)

journal_entries:
  issue_materials:
    description: "Pengeluaran bahan baku"
    debit: "Barang Dalam Proses (WIP)"
    credit: "Persediaan Bahan Baku"
  record_labor:
    description: "Biaya tenaga kerja langsung"
    debit: "Barang Dalam Proses (WIP)"
    credit: "Tenaga Kerja Langsung"
  apply_overhead:
    description: "Alokasi overhead produksi"
    debit: "Barang Dalam Proses (WIP)"
    credit: "Overhead Manufaktur"
  complete_production:
    description: "Penyelesaian produksi"
    debit: "Persediaan Barang Jadi"
    credit: "Barang Dalam Proses (WIP)"

status_flow:
  - from: draft
    to: [planned, cancelled]
    action: plan|cancel
  - from: planned
    to: [released, cancelled]
    action: release|cancel
  - from: released
    to: [in_progress]
    action: start
  - from: in_progress
    to: [completed, on_hold]
    action: complete|hold
  - from: completed
    to: [closed]
    action: close

gaps_found:
  - severity: critical
    description: "NO frontend component exists for Production Orders"
  - severity: critical
    description: "Module NOT defined in actionMenuConstants.ts"
  - severity: major
    description: "BOM (Bill of Materials) expansion UI not available"
  - severity: major
    description: "Material consumption tracking not implemented"
  - severity: major
    description: "Labor/overhead cost tracking not implemented"

recommendations:
  - "Add 'production' module to actionMenuConstants.ts"
  - "Create ProductionPanel.tsx with Gantt/schedule view"
  - "Create ProductionOrderForm.tsx for order creation"
  - "Create MaterialIssueDialog.tsx for material issuance"
  - "Create LaborEntryDialog.tsx for labor tracking"
  - "Implement production costing dashboard"
  - "Add capacity planning view"
```

---

## Module 11: Stock Adjustments (Penyesuaian Stok)

```yaml
module: stock_adjustments
status: FOUND

frontend:
  component_path: /src/components/app/Inventory/StockAdjustmentForm.tsx
  lines: 322
  pattern: modal
  type_file:
    path: /src/types/inventory.ts
    lines: 237

backend:
  router_path: /api_gateway/app/routers/stock_adjustments.py
  lines: 1100+
  schema_path: /api_gateway/app/schemas/stock_adjustments.py

api_endpoints:
  list: GET /api/stock-adjustments
  summary: GET /api/stock-adjustments/summary
  detail: GET /api/stock-adjustments/{id}
  create: POST /api/stock-adjustments
  update: PATCH /api/stock-adjustments/{id}
  delete: DELETE /api/stock-adjustments/{id}
  post: POST /api/stock-adjustments/{id}/post
  void: POST /api/stock-adjustments/{id}/void

form_fields:
  frontend_implemented:
    - frontend_name: newQuantity
      backend_name: new_quantity
      type: number
      required: true
      validation: ">= 0, != current"
    - frontend_name: reason
      backend_name: reason
      type: select
      required: true
      options: [opname, rusak, hilang, koreksi, lainnya]
    - frontend_name: notes
      backend_name: notes
      type: string
      required: false

  backend_available_not_in_frontend:
    - backend_name: adjustment_number
      type: string
      description: "Auto-generated"
    - backend_name: adjustment_date
      type: date
      description: "Defaults to today"
    - backend_name: adjustment_type
      type: enum (increase|decrease|recount|damaged|expired)
    - backend_name: storage_location_id
      type: uuid
    - backend_name: reference_no
      type: string
    - backend_name: items[]
      type: array
      description: "Multiple items adjustment not supported in current form"

journal_entries:
  increase:
    description: "Penambahan stok"
    debit: "Persediaan Barang Dagang (1-10400)"
    credit: "Penyesuaian Persediaan (5-10200)"
  decrease:
    description: "Pengurangan stok"
    debit: "Penyesuaian Persediaan (5-10200)"
    credit: "Persediaan Barang Dagang (1-10400)"

status_flow:
  - from: draft
    to: [posted]
    action: post
    endpoint: POST /api/stock-adjustments/{id}/post
  - from: posted
    to: [void]
    action: void
    endpoint: POST /api/stock-adjustments/{id}/void

current_frontend_features:
  - "Single product adjustment (via ProductCard)"
  - "Reason selection with descriptions"
  - "Notes field"
  - "Live difference calculation"
  - "Success animation"

gaps_found:
  - severity: minor
    description: "Frontend only supports single-item quick adjustment via product card"
  - severity: major
    description: "Full adjustment form with multiple items not available"
  - severity: minor
    description: "Storage location selection not available"
  - severity: minor
    description: "Adjustment list view not available"

recommendations:
  - "Create StockAdjustmentPanel.tsx for full adjustment management"
  - "Add multi-item adjustment support"
  - "Add storage location/warehouse selection"
  - "Add adjustment history list view with status filters"
```

---

## Module 12: Stock Transfers (Transfer Stok)

```yaml
module: stock_transfers
status: NOT_FOUND

frontend:
  component_path: null
  lines: 0
  pattern: null
  module_definition:
    location: /src/components/app/ChatPanel/actionMenuConstants.ts:168
    module_id: transfer_stok
    category: persediaan

backend:
  router_path: /api_gateway/app/routers/stock_transfers.py
  lines: 500+
  schema_path: /api_gateway/app/schemas/stock_transfers.py
  note: "NO JOURNAL ENTRIES - internal stock movement"

api_endpoints:
  list: GET /api/stock-transfers
  in_transit: GET /api/stock-transfers/in-transit
  detail: GET /api/stock-transfers/{transfer_id}
  create: POST /api/stock-transfers
  update: PATCH /api/stock-transfers/{transfer_id}
  delete: DELETE /api/stock-transfers/{transfer_id}
  ship: POST /api/stock-transfers/{transfer_id}/ship
  receive: POST /api/stock-transfers/{transfer_id}/receive
  cancel: POST /api/stock-transfers/{transfer_id}/cancel

form_fields:
  header:
    - frontend_name: null
      backend_name: transfer_number
      type: string
      required: false (auto-generated)
    - frontend_name: null
      backend_name: transfer_date
      type: date
      required: true
    - frontend_name: null
      backend_name: from_warehouse_id
      type: uuid
      required: true
    - frontend_name: null
      backend_name: to_warehouse_id
      type: uuid
      required: true
    - frontend_name: null
      backend_name: notes
      type: string
      required: false

  line_items:
    - frontend_name: null
      backend_name: product_id
      type: uuid
      required: true
    - frontend_name: null
      backend_name: quantity
      type: decimal
      required: true
    - frontend_name: null
      backend_name: unit
      type: string
      required: false
    - frontend_name: null
      backend_name: batch_number
      type: string
      required: false (for batch-tracked items)
    - frontend_name: null
      backend_name: serial_number
      type: string
      required: false (for serialized items)

status_flow:
  - from: draft
    to: [in_transit, cancelled]
    action: ship|cancel
  - from: in_transit
    to: [received, cancelled]
    action: receive|cancel

button_actions:
  - label: "Kirim"
    action: ship
    endpoint: POST /api/stock-transfers/{id}/ship
    condition: status == 'draft'
  - label: "Terima"
    action: receive
    endpoint: POST /api/stock-transfers/{id}/receive
    condition: status == 'in_transit'
  - label: "Batalkan"
    action: cancel
    endpoint: POST /api/stock-transfers/{id}/cancel
    condition: status != 'received'

gaps_found:
  - severity: major
    description: "NO frontend component exists for Stock Transfers"
  - severity: major
    description: "In-transit tracking dashboard not available"
  - severity: minor
    description: "Batch/serial selection not available"

recommendations:
  - "Create StockTransferPanel.tsx with list/detail views"
  - "Create StockTransferForm.tsx for transfer creation"
  - "Add in-transit dashboard showing pending receives"
  - "Implement batch/serial selection for tracked items"
  - "Add warehouse picker component"
```

---

## Module 13: Credit Notes (Nota Kredit)

```yaml
module: credit_notes
status: NOT_FOUND

frontend:
  component_path: null
  lines: 0
  pattern: null
  module_definition:
    location: /src/components/app/ChatPanel/actionMenuConstants.ts:177
    module_id: nota_kredit
    category: penjualan

backend:
  router_path: /api_gateway/app/routers/credit_notes.py
  lines: 1500+
  schema_path: /api_gateway/app/schemas/credit_notes.py
  account_codes:
    ar_account: "1-10300"  # Piutang Usaha
    sales_return: "4-10300"  # Retur Penjualan
    tax_payable: "2-10300"  # PPN Keluaran

api_endpoints:
  list: GET /api/credit-notes
  summary: GET /api/credit-notes/summary
  detail: GET /api/credit-notes/{id}
  create: POST /api/credit-notes
  update: PATCH /api/credit-notes/{id}
  delete: DELETE /api/credit-notes/{id}
  post: POST /api/credit-notes/{id}/post
  apply: POST /api/credit-notes/{id}/apply
  refund: POST /api/credit-notes/{id}/refund
  void: POST /api/credit-notes/{id}/void

form_fields:
  header:
    - frontend_name: null
      backend_name: customer_id
      type: uuid
      required: true
    - frontend_name: null
      backend_name: credit_note_number
      type: string
      required: false (auto-generated)
    - frontend_name: null
      backend_name: credit_note_date
      type: date
      required: true
    - frontend_name: null
      backend_name: original_invoice_id
      type: uuid
      required: false
    - frontend_name: null
      backend_name: reason
      type: string
      required: true
    - frontend_name: null
      backend_name: notes
      type: string
      required: false

  line_items:
    - frontend_name: null
      backend_name: product_id
      type: uuid
      required: true
    - frontend_name: null
      backend_name: quantity
      type: decimal
      required: true
    - frontend_name: null
      backend_name: unit_price
      type: integer
      required: true
    - frontend_name: null
      backend_name: discount_percent
      type: decimal
      required: false
    - frontend_name: null
      backend_name: discount_amount
      type: integer
      required: false
    - frontend_name: null
      backend_name: tax_rate
      type: decimal
      required: false
    - frontend_name: null
      backend_name: tax_amount
      type: integer
      required: false (calculated)
    - frontend_name: null
      backend_name: total
      type: integer
      required: false (calculated)

  application:
    - frontend_name: null
      backend_name: invoice_id
      type: uuid
      required: true
    - frontend_name: null
      backend_name: applied_amount
      type: integer
      required: true

journal_entries:
  post:
    description: "Posting nota kredit"
    debit: "Retur Penjualan (4-10300)"
    debit2: "PPN Keluaran (2-10300) - if tax"
    credit: "Piutang Usaha (1-10300)"
  apply:
    description: "Aplikasi ke faktur"
    effect: "Reduces invoice balance (no journal)"
  refund:
    description: "Refund ke pelanggan"
    debit: "Piutang Usaha (1-10300)"
    credit: "Kas/Bank"

status_flow:
  - from: draft
    to: [posted]
    action: post
    endpoint: POST /api/credit-notes/{id}/post
  - from: posted
    to: [partial, applied]
    action: apply
    endpoint: POST /api/credit-notes/{id}/apply
  - from: posted
    to: [refunded]
    action: refund
    endpoint: POST /api/credit-notes/{id}/refund
  - from: draft
    to: [void]
    action: void
    endpoint: POST /api/credit-notes/{id}/void

button_actions:
  - label: "Posting"
    action: post
    endpoint: POST /api/credit-notes/{id}/post
    condition: status == 'draft'
  - label: "Terapkan ke Faktur"
    action: apply
    endpoint: POST /api/credit-notes/{id}/apply
    condition: status == 'posted' && amount_remaining > 0
  - label: "Refund"
    action: refund
    endpoint: POST /api/credit-notes/{id}/refund
    condition: status == 'posted' && amount_remaining > 0
  - label: "Batalkan"
    action: void
    endpoint: POST /api/credit-notes/{id}/void
    condition: status == 'draft' OR (status == 'posted' && not applied)

gaps_found:
  - severity: critical
    description: "NO frontend component exists for Credit Notes"
  - severity: major
    description: "Invoice application UI not available"
  - severity: major
    description: "Refund processing UI not available"

recommendations:
  - "Create CreditNotePanel.tsx with list/detail views"
  - "Create CreditNoteForm.tsx for credit note creation"
  - "Create ApplyToInvoiceDialog.tsx for invoice application"
  - "Create RefundDialog.tsx for refund processing"
  - "Link from original invoice for quick returns"
```

---

## Module 14: Vendor Credits (Nota Debit)

```yaml
module: vendor_credits
status: NOT_FOUND

frontend:
  component_path: null
  lines: 0
  pattern: null
  module_definition:
    location: /src/components/app/ChatPanel/actionMenuConstants.ts:184
    module_id: nota_debit
    category: pembelian

backend:
  router_path: /api_gateway/app/routers/vendor_credits.py
  lines: 1500+
  schema_path: /api_gateway/app/schemas/vendor_credits.py
  account_codes:
    ap_account: "2-10100"  # Hutang Usaha
    purchase_return: "5-10300"  # Retur Pembelian
    tax_receivable: "1-10700"  # PPN Masukan

api_endpoints:
  list: GET /api/vendor-credits
  summary: GET /api/vendor-credits/summary
  detail: GET /api/vendor-credits/{id}
  create: POST /api/vendor-credits
  update: PATCH /api/vendor-credits/{id}
  delete: DELETE /api/vendor-credits/{id}
  post: POST /api/vendor-credits/{id}/post
  apply: POST /api/vendor-credits/{id}/apply
  receive_refund: POST /api/vendor-credits/{id}/receive-refund
  void: POST /api/vendor-credits/{id}/void

form_fields:
  header:
    - frontend_name: null
      backend_name: vendor_id
      type: uuid
      required: true
    - frontend_name: null
      backend_name: credit_number
      type: string
      required: false (auto-generated)
    - frontend_name: null
      backend_name: credit_date
      type: date
      required: true
    - frontend_name: null
      backend_name: original_bill_id
      type: uuid
      required: false
    - frontend_name: null
      backend_name: reason
      type: string
      required: true
    - frontend_name: null
      backend_name: notes
      type: string
      required: false
    - frontend_name: null
      backend_name: ref_no
      type: string
      required: false

  line_items:
    - frontend_name: null
      backend_name: product_id
      type: uuid
      required: true
    - frontend_name: null
      backend_name: quantity
      type: decimal
      required: true
    - frontend_name: null
      backend_name: unit_price
      type: integer
      required: true
    - frontend_name: null
      backend_name: discount_percent
      type: decimal
      required: false
    - frontend_name: null
      backend_name: discount_amount
      type: integer
      required: false
    - frontend_name: null
      backend_name: tax_rate
      type: decimal
      required: false
    - frontend_name: null
      backend_name: tax_amount
      type: integer
      required: false (calculated)
    - frontend_name: null
      backend_name: total
      type: integer
      required: false (calculated)

journal_entries:
  post:
    description: "Posting kredit vendor"
    debit: "Hutang Usaha (2-10100)"
    credit: "Retur Pembelian (5-10300)"
    credit2: "PPN Masukan (1-10700) - if tax"
  apply:
    description: "Aplikasi ke tagihan"
    effect: "Reduces bill balance (no journal)"
  receive_refund:
    description: "Terima refund dari vendor"
    debit: "Kas/Bank"
    credit: "Hutang Usaha (2-10100)"

status_flow:
  - from: draft
    to: [posted]
    action: post
  - from: posted
    to: [partial, applied]
    action: apply
  - from: posted
    to: [refunded]
    action: receive_refund
  - from: draft
    to: [void]
    action: void

button_actions:
  - label: "Posting"
    action: post
    endpoint: POST /api/vendor-credits/{id}/post
    condition: status == 'draft'
  - label: "Terapkan ke Tagihan"
    action: apply
    endpoint: POST /api/vendor-credits/{id}/apply
    condition: status == 'posted' && amount_remaining > 0
  - label: "Terima Refund"
    action: receive_refund
    endpoint: POST /api/vendor-credits/{id}/receive-refund
    condition: status == 'posted' && amount_remaining > 0
  - label: "Batalkan"
    action: void
    endpoint: POST /api/vendor-credits/{id}/void
    condition: status == 'draft' OR (status == 'posted' && not applied)

gaps_found:
  - severity: critical
    description: "NO frontend component exists for Vendor Credits"
  - severity: major
    description: "Bill application UI not available"
  - severity: major
    description: "Refund receipt UI not available"

recommendations:
  - "Create VendorCreditPanel.tsx with list/detail views"
  - "Create VendorCreditForm.tsx for credit creation"
  - "Create ApplyToBillDialog.tsx for bill application"
  - "Create ReceiveRefundDialog.tsx for refund receipt"
  - "Link from original bill for quick returns"
```

---

## Module 15: Bank Accounts & Transfers

```yaml
module: bank_accounts_transfers
status: PARTIAL

frontend:
  component_path: /src/components/app/KasBank/KasBankPanel.tsx
  lines: 1137
  pattern: tab-based panel
  sub_components:
    - ModalPriveForm.tsx (637 lines)
    - PendapatanBungaForm.tsx (580 lines)
    - PendapatanLainForm.tsx (587 lines)
    - BebanDibayarDiMukaForm.tsx (431 lines)
  type_file:
    path: /src/types/kasbank.ts
    lines: 104

backend:
  bank_accounts:
    router_path: /api_gateway/app/routers/bank_accounts.py
    lines: 1000+
  bank_transfers:
    router_path: /api_gateway/app/routers/bank_transfers.py
    lines: 1000+

api_endpoints:
  # Bank Accounts
  bank_accounts_list: GET /api/bank-accounts
  bank_account_detail: GET /api/bank-accounts/{id}
  bank_account_transactions: GET /api/bank-accounts/{id}/transactions
  bank_account_balance: GET /api/bank-accounts/{id}/balance
  bank_account_create: POST /api/bank-accounts
  bank_account_update: PATCH /api/bank-accounts/{id}
  bank_account_delete: DELETE /api/bank-accounts/{id}
  bank_account_adjust: POST /api/bank-accounts/{id}/adjust

  # Bank Transfers
  transfers_list: GET /api/bank-transfers
  transfer_summary: GET /api/bank-transfers/summary
  transfer_detail: GET /api/bank-transfers/{id}
  transfer_create: POST /api/bank-transfers
  transfer_update: PATCH /api/bank-transfers/{id}
  transfer_delete: DELETE /api/bank-transfers/{id}
  transfer_post: POST /api/bank-transfers/{id}/post
  transfer_void: POST /api/bank-transfers/{id}/void

form_fields:
  bank_account_frontend:
    - frontend_name: newAccountName
      backend_name: account_name
      type: string
      required: true
    - frontend_name: newAccountType
      backend_name: account_type
      type: select
      options: [kas, bank, ewallet]
      required: true
    - frontend_name: newAccountBank
      backend_name: bank_name
      type: string
      required: false (for bank type)
    - frontend_name: newAccountRekening
      backend_name: account_number
      type: string
      required: false (for bank type)
    - frontend_name: newAccountSaldo
      backend_name: opening_balance
      type: number
      required: false

  bank_account_backend_not_in_frontend:
    - backend_name: coa_id
      type: uuid
      description: "Link to Chart of Accounts"
    - backend_name: currency
      type: string
    - backend_name: is_default
      type: boolean

  transfer_frontend:
    - frontend_name: transferFrom
      backend_name: from_bank_id
      type: select
      required: true
    - frontend_name: transferTo
      backend_name: to_bank_id
      type: select
      required: true
    - frontend_name: transferAmount
      backend_name: amount
      type: number
      required: true
    - frontend_name: transferNote
      backend_name: notes
      type: string
      required: false

  transfer_backend_not_in_frontend:
    - backend_name: transfer_number
      type: string
      description: "Auto-generated"
    - backend_name: transfer_date
      type: date
    - backend_name: fee_amount
      type: integer
      description: "Transfer fee"
    - backend_name: fee_account_id
      type: uuid
      description: "Fee expense account"
    - backend_name: ref_no
      type: string

journal_entries:
  transfer:
    description: "Transfer antar bank"
    debit: "Bank Tujuan"
    credit: "Bank Asal"
  transfer_with_fee:
    description: "Transfer dengan biaya"
    debit: "Bank Tujuan + Biaya Transfer (5-20950)"
    credit: "Bank Asal"

current_frontend_features:
  - "Account list with type icons"
  - "Total balance summary"
  - "Simple transfer between accounts"
  - "Add new account (kas/bank/ewallet)"
  - "Account transaction history"
  - "Equity tab (modal/prive)"
  - "Pendapatan tab (bunga/lain)"

gaps_found:
  - severity: major
    description: "Transfer fee handling not available in frontend"
  - severity: major
    description: "Transfer date selection not available"
  - severity: minor
    description: "CoA linking not available in account creation"
  - severity: minor
    description: "Currency selection not available"
  - severity: minor
    description: "Bank reconciliation not in KasBank"

recommendations:
  - "Add transfer fee field and expense account selection"
  - "Add transfer date picker"
  - "Add CoA link dropdown for accounting integration"
  - "Consider adding bank reconciliation entry point"
  - "Add transfer number display/search"
```

---

## Module 16: Customer Deposits (Uang Muka Pelanggan)

```yaml
module: customer_deposits
status: PARTIAL

frontend:
  component_path: /src/components/app/Debt/UangMukaPelangganForm.tsx
  lines: 487
  pattern: fullscreen_form
  type_file:
    path: /src/types/uangMuka.ts
    lines: 102

backend:
  router_path: /api_gateway/app/routers/customer_deposits.py
  lines: 1500+
  schema_path: /api_gateway/app/schemas/customer_deposits.py
  account_codes:
    deposit_account: "2-10400"  # Uang Muka Pelanggan (Liability)
    ar_account: "1-10300"  # Piutang Usaha

api_endpoints:
  list: GET /api/customer-deposits
  summary: GET /api/customer-deposits/summary
  detail: GET /api/customer-deposits/{id}
  create: POST /api/customer-deposits
  update: PATCH /api/customer-deposits/{id}
  delete: DELETE /api/customer-deposits/{id}
  post: POST /api/customer-deposits/{id}/post
  apply: POST /api/customer-deposits/{id}/apply
  refund: POST /api/customer-deposits/{id}/refund
  void: POST /api/customer-deposits/{id}/void
  customer_deposits: GET /api/customers/{id}/deposits

form_fields:
  frontend_implemented:
    - frontend_name: nama_pelanggan
      backend_name: customer_name
      type: string
      required: true
    - frontend_name: kontak_pelanggan
      backend_name: customer_phone
      type: string
      required: false
    - frontend_name: nominal_dp
      backend_name: amount
      type: number
      required: true
    - frontend_name: tanggal_dp
      backend_name: deposit_date
      type: date
      required: true
    - frontend_name: barang_pesanan
      backend_name: description
      type: string
      required: false
    - frontend_name: keterangan
      backend_name: notes
      type: string
      required: false

  backend_available_not_in_frontend:
    - backend_name: customer_id
      type: uuid
      description: "Link to customer master (frontend uses name only)"
    - backend_name: deposit_number
      type: string
      description: "Auto-generated"
    - backend_name: payment_method
      type: string
      description: "cash|transfer|ewallet etc"
    - backend_name: bank_account_id
      type: uuid
      description: "Bank receiving the deposit"
    - backend_name: reference
      type: string

  application:
    - backend_name: invoice_id
      type: uuid
      required: true
    - backend_name: applied_amount
      type: integer
      required: true
    - backend_name: applied_date
      type: date
      required: false

journal_entries:
  receive:
    description: "Terima uang muka"
    debit: "Kas/Bank (1-10100/1-10200)"
    credit: "Uang Muka Pelanggan (2-10400)"
  apply:
    description: "Aplikasi ke faktur"
    debit: "Uang Muka Pelanggan (2-10400)"
    credit: "Piutang Usaha (1-10300)"
  refund:
    description: "Refund ke pelanggan"
    debit: "Uang Muka Pelanggan (2-10400)"
    credit: "Kas/Bank"

status_flow:
  - from: draft
    to: [posted]
    action: post
    endpoint: POST /api/customer-deposits/{id}/post
  - from: posted
    to: [partial, applied]
    action: apply
    endpoint: POST /api/customer-deposits/{id}/apply
  - from: posted
    to: [refunded]
    action: refund
    endpoint: POST /api/customer-deposits/{id}/refund
  - from: draft
    to: [void]
    action: void
    endpoint: POST /api/customer-deposits/{id}/void

current_frontend_features:
  - "Customer name input (not linked to master)"
  - "Contact number field"
  - "Deposit amount with currency formatting"
  - "Deposit date (max today)"
  - "Item description field"
  - "Notes field"
  - "SAK EMKM 2103 information box"
  - "Success animation"

gaps_found:
  - severity: major
    description: "Customer selector not linked to customer master"
  - severity: major
    description: "Invoice application UI not available"
  - severity: major
    description: "Refund processing UI not available"
  - severity: major
    description: "Deposit list view not available"
  - severity: minor
    description: "Payment method selection not available"
  - severity: minor
    description: "Bank account selection not available"

recommendations:
  - "Add customer autocomplete/picker linked to customer master"
  - "Create CustomerDepositPanel.tsx for list management"
  - "Create ApplyDepositDialog.tsx for invoice application"
  - "Create RefundDepositDialog.tsx for refund processing"
  - "Add payment method selector"
  - "Add bank account selector for deposit destination"
```

---

## Component Location Map

```
/root/milkyhoop/frontend/web/src/
├── components/
│   └── app/
│       ├── ChatPanel/
│       │   └── actionMenuConstants.ts    # Module definitions (51 modules)
│       ├── MoreModules/
│       │   └── index.tsx                 # Module category display
│       ├── Inventory/
│       │   ├── InventoryPanel.tsx        # Main inventory panel
│       │   ├── StockAdjustmentForm.tsx   # ✅ FOUND (322 lines)
│       │   ├── ReturForm.tsx             # Return form
│       │   ├── AsetTetapForm.tsx         # Fixed asset form
│       │   ├── AddProductForm.tsx        # Add product
│       │   └── ProductListItem.tsx       # Product list item
│       ├── Debt/
│       │   ├── DebtPanel.tsx             # Main debt panel
│       │   ├── UangMukaPelangganForm.tsx # ✅ FOUND (487 lines)
│       │   ├── UangMukaPembelianForm.tsx # Vendor deposit form
│       │   ├── PiutangUsahaForm.tsx      # AR form
│       │   ├── HutangUsahaForm.tsx       # AP form
│       │   └── ...
│       ├── KasBank/
│       │   ├── KasBankPanel.tsx          # ✅ FOUND (1137 lines)
│       │   ├── ModalPriveForm.tsx        # Modal/Prive entry
│       │   ├── PendapatanBungaForm.tsx   # Interest income
│       │   ├── PendapatanLainForm.tsx    # Other income
│       │   └── BebanDibayarDiMukaForm.tsx# Prepaid expense
│       ├── SalesInvoice/                 # ✅ EXISTS (5005 lines)
│       ├── PurchaseInvoice/              # ✅ EXISTS (6896 lines)
│       ├── Expenses/                     # ✅ EXISTS (3632 lines)
│       ├── Vendor/                       # ✅ EXISTS (3754 lines)
│       ├── Customer/                     # ✅ EXISTS (706 lines)
│       │
│       # ❌ MISSING COMPONENTS:
│       ├── Quotes/                       # ❌ NOT FOUND
│       ├── SalesOrders/                  # ❌ NOT FOUND
│       ├── PurchaseOrders/               # ❌ NOT FOUND
│       ├── Production/                   # ❌ NOT FOUND
│       ├── StockTransfers/               # ❌ NOT FOUND
│       ├── CreditNotes/                  # ❌ NOT FOUND
│       └── VendorCredits/                # ❌ NOT FOUND
│
└── types/
    ├── inventory.ts                      # ✅ Stock adjustment types
    ├── kasbank.ts                        # ✅ Bank account types
    ├── uangMuka.ts                       # ✅ Deposit types
    ├── purchase.ts                       # ⚠️ Partial PO types
    └── ...
```

---

## Gap Analysis Summary

### Critical Gaps (Frontend Component Missing)

| Module | Backend Ready | Action Required |
|--------|--------------|-----------------|
| Quotes | ✅ | Create QuotesPanel + QuoteForm |
| Sales Orders | ✅ | Create SalesOrderPanel + SalesOrderForm + ShipmentDialog |
| Purchase Orders | ✅ | Create PurchaseOrderPanel + PurchaseOrderForm + ReceiveDialog |
| Production Orders | ✅ | Create ProductionPanel + ProductionOrderForm + MaterialIssueDialog |
| Credit Notes | ✅ | Create CreditNotePanel + CreditNoteForm + ApplyDialog |
| Vendor Credits | ✅ | Create VendorCreditPanel + VendorCreditForm + ApplyDialog |

### Major Gaps (Partial Implementation)

| Module | Issue | Action Required |
|--------|-------|-----------------|
| Stock Adjustments | Single-item only | Create full adjustment panel with multi-item |
| Stock Transfers | No component | Create StockTransferPanel + StockTransferForm |
| Bank Transfers | No fee handling | Add fee field and account selector |
| Customer Deposits | No list view | Create CustomerDepositPanel + Apply/Refund dialogs |

### Minor Gaps (Enhancement Needed)

| Module | Issue | Action Required |
|--------|-------|-----------------|
| Stock Adjustments | No warehouse/location | Add location selector |
| Bank Accounts | No CoA linking | Add CoA dropdown |
| Customer Deposits | No customer link | Add customer autocomplete |

---

## Recommendations Priority

### Phase 1: Critical (7 modules)
1. **Quotes** - Essential for sales workflow
2. **Sales Orders** - Order management before invoicing
3. **Purchase Orders** - Procurement workflow
4. **Credit Notes** - Sales returns/adjustments
5. **Vendor Credits** - Purchase returns/adjustments
6. **Stock Transfers** - Multi-warehouse operations
7. **Production Orders** - Manufacturing (if relevant)

### Phase 2: Major Improvements
1. Multi-item stock adjustment form
2. Customer deposit list and apply/refund
3. Bank transfer fee handling

### Phase 3: Minor Enhancements
1. CoA linking in bank accounts
2. Customer autocomplete in deposits
3. Storage location in adjustments

---

## Notes

- All backend APIs are ready and fully functional
- Frontend uses chat-based form submission pattern in some cases
- Module IDs are defined in `actionMenuConstants.ts` but components not created
- Consistent design pattern: fullscreen forms for data entry, panels for list views
- All monetary values use integer (Rupiah, no decimals)
- Journal entries created on "post" action, not on draft creation

---

*Report generated by Claude AI - Frontend Analyst*
