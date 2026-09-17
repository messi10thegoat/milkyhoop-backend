# Powerful Search — Backend Contract (v1, 2026-09-17)

Engine: Postgres `pg_trgm` + `unaccent`, GIN(`gin_trgm_ops`) on a trigger-maintained
`search_text` column per table. NO Elasticsearch/Meilisearch. One endpoint serves the
⌘K global palette (scope=all) and the per-module pills (scope=<type>).

## Endpoint
`GET /api/search?q=<str>&scope=<all|TYPE>&limit=<int>`
- Auth: Bearer JWT — tenant_id + user from token (never a query param).
- `q`: trimmed; **min 2 chars** → otherwise `200 {"query":q,"groups":[]}`.
- `scope`: omitted or `all` → all groups. Or exactly one TYPE key (table below).
- `limit`: items **per group**. Default 8, clamped 1..20. (⌘K palette sends `limit=15`.)
- Palette compatibility: `GET /api/search?q=…&limit=15` (no scope) works = scope all.
- Match: `search_text LIKE '%'||lower(unaccent(q))||'%'` (trigram GIN). Order:
  `similarity(search_text, norm) DESC, <date> DESC`. One request,
  `SET LOCAL statement_timeout='500ms'`.
- **Customer cross-match**: documents whose `customer_id` belongs to a customer matching
  `q` are ALSO returned (even if the doc's own text doesn't match), `matched_field="customer"`.
  Deliveries reach the customer via `sales_order_id → sales_orders.customer_id`.
- Authz: per group `policy_engine.can('R', module)`; groups the user cannot read are
  **omitted** (not error). Tenant is always filtered.

## Response shape
```json
{ "query": "jefri",
  "groups": [
    { "type": "customers", "count": 1, "items": [
      { "id": "uuid", "type": "customers", "title": "Jefri Kolondam",
        "subtitle": "PLN LANGOWAN, Langowan", "number": "C-0007",
        "amount": null, "status": null, "due_date": null,
        "url_hint": "/kontak/pelanggan/uuid", "matched_field": "address" }
    ]}
  ] }
```
Every item carries ALL keys (null where N/A): `id, type, title, subtitle, number,
amount(number|null), status, due_date(YYYY-MM-DD|null), url_hint, matched_field`.
`matched_field` ∈ {direct text field name, "customer"}.

## Scope TYPE keys, field mapping, url_hint
| type | authz module | title | subtitle | number | amount | status | due_date | url_hint |
|---|---|---|---|---|---|---|---|---|
| customers | customer | name/nama | address + phone | code | — | — | — | /kontak/pelanggan/{id} |
| vendors | supplier | name | address + phone | code | — | — | — | /kontak/vendor/{id} |
| sales_invoices | sales_invoice | customer_name | invoice_number | invoice_number | total_amount | status | due_date | /penjualan/faktur/{id} |
| quotes | sales_order | customer_name | subject | quote_number | total_amount | status | expiry_date | /penjualan/penawaran/{id} |
| sales_orders | sales_order | customer_name | reference | order_number | total_amount | status | expected_ship_date | /penjualan/pesanan/{id} |
| deliveries | sales_order | shipment_number | carrier + tracking_number | shipment_number | — | status | shipment_date | /penjualan/pengiriman/{id} |
| receive_payments | receive_payment | customer_name | payment_number | payment_number | total_amount | status | payment_date | /penjualan/pembayaran/{id} |
| customer_deposits | receive_payment | customer_name | deposit_number | deposit_number | amount | status | deposit_date | /penjualan/uang-muka/{id} |
| credit_notes | sales_invoice | customer_name | credit_note_number | credit_note_number | total_amount | status | credit_note_date | /penjualan/nota-kredit/{id} |

Group order in `scope=all`: customers, vendors, sales_invoices, sales_orders, quotes,
deliveries, receive_payments, customer_deposits, credit_notes.

## Pills: `?search=` on list endpoints → search_text
List endpoints upgraded so `?search=` matches `search_text` (address/notes/all attrs),
pagination/filters unchanged (confirmed set in the final deploy report):
- GET /api/customers?search=
- GET /api/sales-invoices?search=
- GET /api/vendors (suppliers) ?search=

## search_text coverage
- customers: code,name,nama,display_name,company_name,contact_person,phone,phone2,telepon,
  mobile_phone,email,address,alamat,city,province,postal_code,tax_id,nik,nomor_member,
  community,website,notes
- vendors: code,name,display_name,company_name,contact_person,phone,mobile_phone,email,
  address,city,province,postal_code,tax_id,nik,bank_name,bank_account_number,
  bank_account_holder,notes
- documents: own number + customer_name snapshot + reference/ref_no + notes (+ subject for
  quotes, reason/reason_detail for credit_notes). Customer address/notes reach documents via
  the customer cross-match, not the doc's own search_text.
