# PRODUCT BLOCKER: delivery-mode revenue recognition exists in code but is USER-UNREACHABLE

**Date:** 2026-07-26
**Type:** PRODUCT BLOCKER (target-market fit — the konveksi/delivery-mode segment) + E2E blocker for the DP spec.
**Surfaced by:** FASE-4 step 5 pre-flight (avoiding the auto-fulfill trap).

## The capability and the trap
`post_invoice` (sales_invoices.py:1704-1727) chooses the revenue path by `effective_policy`:
- `all_have_cost && policy=='delivery'` -> **DEFER** (Event 1 only at post; COGS+revenue at /fulfill).
- `all_have_cost` (else) -> **AUTO-FULFILL**: Event 1+2+3 atomically at post (sell-from-stock).

`effective_policy = invoice.recognize_at OR tenant_config.revenue_recognition_policy OR 'invoice'`.

For a sell-from-stock item (Kaos Biru: WAC 35.000, stock 100, sell 100 — `all_have_cost=true`), the
default `'invoice'` policy AUTO-FULFILLS: posting the invoice immediately recognizes revenue 5.000.000
+ COGS 3.500.000 and marks it `fulfilled`. That is the OPPOSITE of the konveksi "delivery mode"
(stock leaves at Pengiriman, not at faktur) the owner described.

## The blocker: there is NO user-reachable way to select 'delivery'
Both levers exist in the backend but neither is reachable:
| lever | reachable? | evidence |
|---|---|---|
| `tenant_config.revenue_recognition_policy = 'delivery'` | **NO** | grep of the whole app: **zero** INSERT/UPDATE to tenant_config / revenue_recognition_policy (only READ at sales_invoices.py:1707). onboarding_service creates **no** tenant_config row (our tenant has 0 rows). No settings API, no settings UI. |
| per-invoice `recognize_at='delivery'` | **NO (via UI)** | the API accepts it (schemas/sales_invoices.py:137; sales_orders to-invoice :1298), but **no FE form sends it** — ConvertToInvoiceFormDesktop posts only `{invoice_date,due_date,items}`; SalesInvoice create form has zero `recognize_at` references. |
| item-level defer flag | none | no item/product defer lever exists. |

So: **the P4/B2 delivery-mode work shipped the engine but never wired a control to it.** Every tenant
is born `'invoice'` (auto-fulfill) with no way out through the product. A konveksi business — whose
whole model is recognize-at-delivery — cannot configure it.

## Impact
- The 3-event PSAK-72 deferral (a headline capability) is inert for all tenants via the UI.
- For the DP E2E: step 5 would auto-fulfill, recognizing revenue+COGS at faktur, contradicting the
  spec (Event 1 only), leaving step 7 with nothing to ship, and — critically — leaving the
  voided_reason / GET fulfillments gate untestable (nothing shippable remains).

## Fix options (owner decision)
1. Add a **settings control** (API + UI) to set `tenant_config.revenue_recognition_policy`
   ('invoice' | 'delivery'), and have onboarding seed it (konveksi template -> 'delivery').
2. And/or expose a **per-invoice recognize_at toggle** in the faktur form (the API already accepts it).
3. Decide the default for the konveksi onboarding template.

## Harness decision needed (do not improvise)
To run step 5 deferred (per spec) the harness must force 'delivery' by one of:
- (A) set `tenant_config.revenue_recognition_policy='delivery'` (raw write; represents a konveksi
  delivery-mode tenant; then the REAL FE to-invoice path defers) — add to step_-1 as documented
  provisioning (same class as the is_pkp API-gap). MOST faithful to the tenant model.
- (B) pass `recognize_at='delivery'` per-invoice to /to-invoice — API-supported but the FE never
  does this, so it diverges from the user path.
Neither is a user-reachable path; this is the product blocker itself.
