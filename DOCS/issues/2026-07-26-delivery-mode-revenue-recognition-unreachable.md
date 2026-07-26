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

---
## HARDENING (2026-07-26, post step-5 proof)

### (a) Pengiriman module is DEAD for every fresh tenant — closes the loop to this session's origin bug
Delivery/fulfill (Pengiriman) is only reachable for an invoice in `revenue_status=deferred`
(fulfillment_status=pending). With no user-reachable way to set delivery mode, a fresh tenant's
invoices always auto-fulfill at post → **nothing is ever `pending` → the Pengiriman module has no
input → it is unreachable.** This closes the loop to the bug that STARTED this whole effort: the
`GET /fulfillments` 500 we called "universal, all tenants". It is universal only among tenants that
can *reach* the fulfillment form — and on a fresh install, none can. The fix f5cd41a5 has still
never been exercised over HTTP; step 7 (this harness, forced delivery mode) is the first real chance.

### (b) PSAK-72: correct engine, wrong default, no control
The 3-event engine recognizes revenue at transfer of control — correct. For pure sell-from-stock,
`'invoice'` (recognize at billing) is defensible. But for **bill-first / ship-later** businesses
(konveksi, mebel, custom manufacturing — a large share of production UMKM) revenue is recognized
**too early** (at faktur, before delivery) and there is **no control to correct it**. The engine is
compliant; the default is not, and the knob is unreachable. This is a compliance-posture defect for
the exact segment the product targets.

### (c) Regression check — NOT a regression, it is never-wired
git history: `revenue_recognition_policy` was introduced **read-only** in P4 (68926f96) and the
`/to-invoice` recognize_at pass-through in c1ce92d1; **no INSERT/UPDATE to tenant_config was ever
committed**, and no old DB (milkydb_saved / milkydb_goldenpath_green) has ever held a tenant_config
row. So the write path was never built (not lost in recovery). Ticket title stands: "never wired".

### Proof that the workaround is correct (not just green)
With `tenant_config.revenue_recognition_policy='delivery'` set, the REAL FE `/to-invoice` payload
(no recognize_at) produced Event 1 ONLY: Dr 1-10400 5.000.000 / Cr 2-10750 5.000.000,
fulfillment_status=pending, revenue_status=deferred, 4-10100=0, 5-10100=0, inventory untouched.
This confirms both the defer branch AND that the sole missing piece for real users is the control.

---
## CORRECTION to hardening (a) — withdrawn overclaim (2026-07-26, evidence from milkydb_saved)

Hardening point (a) above claimed "Pengiriman module is DEAD for every fresh tenant / GET
/fulfillments unreachable on fresh install." **That is WRONG — withdrawn.** Evidence from
milkydb_saved (the only DBs with real tenant activity):

- Only 2 tenants ever existed there: konveksi-cemerlang, konveksi-bintang-timur (no grapgrap).
- konveksi-cemerlang invoices: 2 `fulfilled/recognized` + 1 `not_applicable`. **Zero deferred.**
  Its items had WAC > 0 (45.000, 71.500) → they **AUTO-FULFILLED** at post (default 'invoice').
- Auto-fulfill **DOES create invoice_fulfillments rows**: konveksi-cemerlang has **3** such rows.

Therefore `GET /fulfillments` (and the schema-drift 500 that started this whole effort) is
reachable from **any posted inventory invoice** via its auto-created fulfillment record — it is
**more** universal, not gated on delivery mode. No delivery-mode / deferred invoice is required to
hit it. (My "loop-closing" framing had the direction backwards.)

### What IS genuinely unreachable (the ticket's real, narrower point — stands)
The **delivery-mode workflow itself**: post an invoice that STAYS `pending`/`deferred` and is
shipped later via a separate Pengiriman action (stock leaves at delivery, not at faktur). No
user-reachable control sets that policy, and **no tenant in any saved DB ever ran it** (0 deferred
invoices anywhere). So the capability is real in code, inert in product — but do not conflate that
with "the fulfillment list/endpoint is unreachable".

### Independent corroboration that the control is missing
`goldenpath.sh` itself must pass `recognize_at:"delivery"` **per invoice** (line 83) to exercise
deferral — precisely because there is no tenant-level way to set it. The project's own golden
harness working around the gap is independent evidence the control was never built.

### Step-7 relevance (unchanged)
This DP harness forces delivery mode, so INV-2607-0001 is `pending`/`deferred` with a shippable
line. Step 7 (manual /fulfill + the voided_reason / GET-fulfillments gate) therefore exercises the
DEFERRED-then-manually-ship path specifically — the path a real user cannot currently reach without
the missing control. That gate value is preserved; the "module is dead" justification for it is not.
