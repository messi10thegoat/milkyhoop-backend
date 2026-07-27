# Step C — compute_ap_outstanding vendor-deposit fix (V218): standalone proof

**DB:** `milkydb_c_test` (clone of LIVE `milkydb` — exact parity, `schema_migrations` 209 pre-existing).
**Migration applied via new pipeline:** `migrate.sh apply` → `newly-applied=1 already-present=209`, tracked `applied_by='runner'` (first real pending migration end-to-end, not backfill).
**Fixtures:** `scripts/e2e/step_c_vendor_deposit_ap/seed.sql` + `verify.sql`.

## The bug
`apply_vendor_deposit` posts `Dr 2-10100 (PAYABLE) / Cr 1-10800`, settling the bill. But `compute_ap_outstanding` built `paid_amount` only from `bill_payment_allocations` + `vendor_credit_applications`. A `DEPOSIT_APPLICATION` journal is in neither → its PAYABLE debit was counted nowhere → **AP overstated by the applied amount** (AR mirror already fixed live: Branch 3 / FIX_P35_ARCANON).

## 5-case regression matrix (before → after V218)

| Case | Bill | Setup | compute_ap BEFORE | compute_ap AFTER | Expected | ✓ |
|------|------|-------|------:|------:|------:|---|
| 1 NULL-handling | BILL-1 | obligation 1.000.000, no deposit | 1.000.000 | 1.000.000 | 1.000.000 (must NOT vanish) | ✓ |
| 2 partial | BILL-2 | oblig 1.000.000, deposit 400.000 | 1.000.000 (bug) | 600.000 | 600.000 | ✓ |
| 3 full | BILL-3 | oblig 1.000.000, deposit 1.000.000 | 1.000.000 (bug) | dropped (0) | 0 → dropped | ✓ |
| 4 all-sources | BILL-4 | oblig 1.000.000, payment 200.000, VC 100.000, deposit 300.000 | 700.000 (bug) | 400.000 | 400.000 | ✓ |
| 5 reversed | BILL-5 | oblig 1.000.000, deposit 500.000 REVERSED (Law-2) | 1.000.000 | 1.000.000 | 1.000.000 | ✓ |

- **Case 1** proves the `LEFT JOIN` + `COALESCE` on every term: the bill with no deposit is neither NULL-ed nor dropped from AP aging (silent-fallback class averted).
- **Case 4** proves **empirically** zero overlap across all four debit sources (obligation/payment/VC/deposit each counted once).
- **Case 5** proves the Law-2 reversal contract: setting `reversed_by_id` on the apply journal removes it from AP.

## Invariant 1 — compute_ap TOTAL vs raw ledger PAYABLE (all POSTED; reversal pairs net)
| | compute_ap total | raw ledger PAYABLE | drift |
|--|------:|------:|------:|
| BEFORE | 4.700.000 | 3.000.000 | **1.700.000** (= 400k+1.000k+300k bug) |
| AFTER  | 3.000.000 | 3.000.000 | **0** |

## Invariant 2 — compute_ap (per bill) vs inline `get_bill_remaining_from_journal`
| Bill | compute_ap | inline | diff |
|------|------:|------:|------:|
| BILL-1 | 1.000.000 | 1.000.000 | 0 |
| BILL-2 | 600.000 | 600.000 | 0 |
| BILL-3 | 0 | 0 | 0 |
| BILL-4 | 400.000 | 400.000 | 0 |
| BILL-5 | 1.000.000 | 500.000 | **500.000** |

**Finding (invariant did its job):** on the reversed case the inline calc `get_bill_remaining_from_journal` (vendor_deposits.py:54-100) disagrees by 500.000 because it filters `je.status='POSTED'` but **omits `je.reversed_by_id IS NULL`** — it keeps counting a reversed settlement. `compute_ap` (with the filter) is correct; the inline calc is wrong on reversal. Dormant today (no vendor-deposit un-apply endpoint) but a landmine once reversal ships. Reinforces the "retire the inline calc, use the canonical function" follow-up.

## UNIQUE precondition (Change 1) — armed
- Pre-check: 0 rows sharing a `journal_id` on either table → safe to ADD.
- `uq_cda_journal_id`, `uq_vda_journal_id` created.
- Negative test: a second `vendor_deposit_applications` row reusing an existing `journal_id` → `ERROR: duplicate key value violates unique constraint "uq_vda_journal_id"`. Fan-out is now structurally impossible, not convention.

## Schema-vs-code drift found while seeding (filed, out of Step-C scope)
- **`apply_vendor_deposit` writes non-existent bill columns**: `UPDATE bills SET paid_amount=..., total_amount=...` — `bills` has `amount_paid` (not `paid_amount`) and **no `total_amount`**. This UPDATE 500s → the endpoint has never successfully run against this schema. Same dead-end family as the customer-side P1 fixes. Vendor-deposit apply needs a column-drift pass before it can be exercised live.

## Provisioning dry-run (FASE-4 preview) — what broke
Seeding a minimal tenant surfaced, in order: (a) accounting tables FK `tenant_id → "Tenant"(id)` for `chart_of_accounts`/`fiscal_periods` (must create Tenant first); (b) `vendor_credits.reason` CHECK enum (`return|pricing_error|discount|damaged|other`); (c) the `bills` column drift above. None block Step C; all are provisioning-path notes for FASE 4.

---

# CORRECTIONS (post-review)

## Ralat verdict (was "active latent")
The AP bug is **NOT "active latent" — it is UNREACHABLE behind a broken write path.**
`apply_vendor_deposit` has never successfully run once (it writes `bills.paid_amount`
/`bills.total_amount`, columns that do not exist → 500). That is *why* the bug was
never exposed in production.

**Explicit decision:** the vendor-deposit feature is **dead end-to-end**. The DP
target flow does not use it → **conscious DEFER**, filed here so it does not hang
undecided. **V218 still ships and is still correct**: the `UNIQUE(journal_id)`
constraints protect the *live* AR side (customer_deposit_applications / Branch 3),
and the AP read path is ready the moment the vendor write path is repaired.

## Refinement of the Invariant-2 (inline) finding — is it active? NO (checked)
Question raised: `get_bill_remaining_from_journal` filters `status=POSTED` without
`reversed_by_id IS NULL`; does that mis-state a LIVE flow (e.g. voided bill payment)?

Checked `void_bill_payment` (bill_payments.py:1587): the reversing journal is created
with **`source_type=BILL_PAYMENT` and `source_id=payment_id`** (same source as the
original), posted POSTED, and `reversed_by_id` set on the original. Consequence:
- inline counts BOTH original (Dr PAYABLE) and reversal (Cr PAYABLE) via its
  `BILL_PAYMENT` branch (which keys on `source_id`) → they **net to 0** → correct.
- compute_ap excludes the original via `reversed_by_id IS NULL`; the reversal is not
  `bpv2.journal_id` → not in payment_debits → also nets to 0 → correct.

So the inline is correct for voided bill payments (and, by the same source-reuse
netting, voided vendor credits). **The divergence is genuinely dormant** — but the
precise reason is sharper than "no reversal endpoint": the inline handles reversals
by *source-reuse netting*, and the **`DEPOSIT_APPLICATION` branch is the one branch
that keys on `je.id IN (vda.journal_id)` instead of `source_id`** — a reversal journal
is never in `vendor_deposit_applications`, so that branch **can never net a reversal**.
The moment vendor-deposit un-apply is implemented (in ANY style) the inline breaks.
Same conclusion, same owner: it lives behind the dead vendor-deposit write path.

## Consumers of get_bill_remaining_from_journal (question b)
NOT a user-facing display. It is **copy-pasted in 3 routers** (bill_payments.py:123,
vendor_deposits.py:54, vendor_credits.py:116 — DRY/drift hazard) and consumed in
WRITE paths to compute `remaining_before` for an allocation: bill_payments.py:1245
(live), vendor_credits.py:1338 (live), vendor_deposits.py:604 (dead). AP/AR *aging*
uses compute_ap/compute_ar, not this function. No user-facing exposure from the
divergence, and the live write-path consumers do not hit the DEPOSIT_APPLICATION-
reversal case.

---

# CORRECTIONS (round 2)

## Case 5 covers convention (ii) ONLY
The fixture set reversed_by_id on the original apply journal by hand — i.e. it exercised
ONLY the reversed_by_id convention, the one this CTE needs. It did NOT exercise the
dominant source-reuse-netting convention (i). Had un-apply been written in style (i) — a
reversal journal with source_type='DEPOSIT_APPLICATION' NOT inserted into
vendor_deposit_applications — the CTE JOIN would never see it, the original would stay
counted, and the deposit would settle the bill forever, silently. V218's REVERSAL
CONTRACT comment now states this (SET reversed_by_id mandatory; do NOT copy
void_bill_payment here). The green is narrower than it looked: correct GIVEN (ii).

## The three copies of get_bill_remaining_from_journal HAVE diverged (item 2)
Not identical — a 4th instance of the ghost/drift class, two on live write paths:
- bill_payments.py:123 (live at :1245) — AP by ROLE: account_roles.role_key='AP_TRADE' (Fase D2.3)
- vendor_credits.py:116 (live at :1338) — same role form, tagged Fase D2.2 (version drift)
- vendor_deposits.py:54 (dead at :604) — STILL hardcoded coa.account_code='2-10100' (Law 27 gap)
On a tenant whose AP_TRADE role != code 2-10100, the copies compute against different
accounts. Retirement collapses all three onto compute_ap/compute_ar — not re-sync 3 copies.

## Reframe of consumer analysis (item 3)
"No user-facing exposure" is true for URGENCY but misleading in kind. This function fills
remaining_before for allocations = audit trail + likely overpayment guard. A wrong value
does not paint a bad number on screen — it can REJECT a legitimate payment or corrupt the
audit trail. Not a reason to escalate; a reason to ACCELERATE retirement onto canonical fns.

---

# ROUND 4 — V219 (quotes columns), oracle-3, CI ratchet, audit-matrix corrections

## FE oracle authority (cond 1)
useQuoteForm.ts + both ItemFormSheet.tsx are UNMODIFIED on Mac (= git master). The recovered
server has NO FE source tree (/root/milkyhoop/frontend/web missing; useQuoteForm found nowhere)
-> only Mac/master exist and are identical for these files; the audit's "72 uncommitted server FE
files" does not apply to this server (FE served as a prebuilt bundle).
RAISED to FASE-5 prerequisite: UI E2E must run against a FE built from a PINNED git commit;
the deployed bundle's provenance cannot be diffed on the server.

## items autocomplete — DO NOT STRIP (cond 2 reversed the earlier plan)
SalesInvoice CreateInvoice/ItemFormSheet.tsx CONSUMES sales_tax_id/sales_tax_name/sales_tax_rate
from /items/autocomplete (types L117-119; prefills invoice-line tax L376-378). Per the FE rule this
is built-but-unmigrated (like quotes), NOT a strip. products has sales_tax(varchar), no sales_tax_id.
DECISION DEFERRED, NOT in V219 scope. Options: (a) add products.sales_tax_id/purchase_tax_id (uuid FK
tax_codes) + populate on create + map; (b) return NULL-shaped tax fields to fix the 500 while keeping
the FE contract (DP flow is non-PKP -> tax null anyway; the broken picker is the real blocker).

## V219 proof (cond 3)
5 columns added; the EXACT quotes.py:502 INSERT (all 27 params + status literal) now succeeds
(INSERT 0 1, valid discount_type) -> column-clean, NO second ghost IN THE STATEMENT. Residual
scanner-invisible surfaces NOT yet tested: quote_items INSERT + GET/response serializer SELECT.
Value-CHECK noted: quotes.discount_type IN {fixed, percentage} only -> quote-create 500s if FE sends
'none'/empty; verify FE default in FASE 4 (value concern, not schema).

## Oracle-3 vs LIVE copy (round-3 condition, now closed)
Re-ran the invariant with the LIVE per-bill code (bill_payments.py role-based AP_TRADE; account_roles
seeded). Per bill: BILL-1..4 == compute_ap (1M/600k/0/400k); BILL-5 500k vs 1M (reversed-case
divergence reproduces). V218 confirmed against the code users actually hit. The earlier caveat
(case-5 oracle used the DEAD copy) is closed.

## CI ratchet (approved) — scripts/schema-contract/
schema_scan.py --signatures + ci_check.sh + baseline_signatures.txt (147 distinct signatures).
Proven: clean -> exit 0; a new ghost -> exit 1 (prints the added signature). Grandfathers the existing
set, fails only on INCREASE, baseline shrinks on cleanup. Prereq: cols.txt refreshed from target DB.
Portability (hardcoded /root paths) = follow-up.

## AUDIT-MATRIX CORRECTIONS (cond 4)
- Step 1 Penawaran: earlier verdict READY is WRONG. Evidence was "[SQL] quotes has N columns" =
  schema-exists used as a proxy for works. Reality: static INSERT, 500 on ANY payload, 0 quotes rows
  in EVERY DB -> the Penawaran module has NEVER created a single quote. (Addressed by V219 + residual
  checks pending.)
- RALAT B4 (2nd time): "required_deposit quote-anchored" does NOT hold — quotes never had rows ->
  dp_amount/dp_percent never populated -> DP was never anchored to anything. Required-deposit-at-quote
  has never existed in practice.
- After V219, step 1 is the RISKIEST E2E step: the only one never executed. Anticipate follow-on
  findings (quote_items, serializer, discount_type default).

---

# ROUND 5 — V007 cascade, FE 72-file resolution, items ADD (V220), step-1 sequence proof

## V007 cascade — RESOLVED (not a blocker)
products.base_unit EXISTS, unit_conversions EXISTS, item_pricing EXISTS on live. The old V007
cascade (memory: base_unit never created -> V008/V194 dead) was HEALED by the recovery patches
(V213-217). Step 0's unit handling has its columns/tables. The memory note is outdated.

## FE 72-file "drift" — CLOSED, no incident, deploy is safe (cond 2)
Correct tree is /root/milkyhoop-dev (NOT /root/milkyhoop = production; my earlier search was the
wrong tree). Findings:
- HEAD == origin/master (f71db830) -> committed state is fully in GitHub.
- 72 porcelain entries = build artifacts + deletions: 51 png (icons), 3 js + 2 css + maps
  (hashed bundles), 3 json + 2 html (manifests), and 4 DELETED .tsx/.ts source files.
- The 4 source entries are DELETIONS; all present in git HEAD (git cat-file -e confirmed) ->
  a deploy `git pull`/reset RESTORES them, does not lose them.
- ZERO untracked frontend files (earlier "1" was a grep-count artifact of an empty string).
- Snapshot taken first (non-destructive): /root/fe-uncommitted-20260725.patch (15 MB, all M/D).
- CONCLUSION: no server-unique source; the restore-agent left nothing unique. Deploy git pull is
  SAFE. Correction to audit + my earlier claim: milkyhoop-dev/frontend/ is the SERVED built
  bundle (+ a partial vendored src/ tracked in git with on-disk deletions); "no FE source tree"
  was imprecise, "72 uncommitted files" = artifacts/deletions, not an incident.

## items autocomplete — ADD (V220), proven (cond 1 of this round)
DECISION: ADD (FE Faktur sheet consumes sales_tax_id/name/rate). Confirmed pre-write:
(a) tax_codes has id/name/rate (+ more); (b) products.sales_tax/purchase_tax = varchar, 0 product
rows on live (no seed) -> clean add; (c) items.py JOINs tax_codes ON st.id = p.sales_tax_id,
selects st.name/st.rate. V220 adds products.sales_tax_id/purchase_tax_id uuid REFERENCES
tax_codes(id). SEPARATE migration (not editing committed V219): immutability + clean provenance.
Legacy varchar sales_tax/purchase_tax left untouched (backlog).
PROOF: after V220 on clone, the EXACT items.py autocomplete query EXECUTES (0 rows, no error) —
[SQL]-level; was a hard 500 (p.sales_tax_id does not exist) before. True HTTP GET /items/
autocomplete -> 200 is a FASE-4 gate (gateway points at live; live-apply held).

## Step-1 create — sequence proven (cond 3)
- quote_items INSERT (quotes.py:599) is STATIC (14 cols). On clone: quotes header INSERT +
  quote_items INSERT both succeed (INSERT 0 1 each) -> step-1 create is SQL-provable end-to-end.
- Create response serializer is SAFE: returns a static dict {id, quote_number, total_amount,
  dp_amount, dp_percent} — reads back no ghost/new column.
- Remaining as a FASE-4 GATE (not residual footnote): POST /quotes with items -> 201 via the app
  (exercises pydantic schema, discount_type default, and the full transaction).

## CI ratchet note
Baseline (147) was built vs LIVE cols. After V219/V220 apply to live, items.py sales_tax_id/
purchase_tax_id (and quotes.* once V219 lands) stop being ghosts -> ci_check reports them as
cleaned-up (GONE); refresh cols.txt + shrink baseline post-deploy.

## STRATEGIC (write-once): every audit-matrix READY verdict is API/journal-level; NONE test UI.
The picker 500 is the first UI-class instance. A curl-scripted FASE-4 harness BYPASSES the picker
entirely and proves the LEDGER, not the UX. SECOND FASE-5 gate required: one UI pass before any
claim that "the flow works for a user."

---

# ROUND 6 — corrections + sentinels

## Deploy does NOT rebuild FE (cond 1)
docker-compose + deploy scripts = backend only (postgres/redis/api_gateway/...); no npm/react
build anywhere. FE served as a static bundle -> the deleted .tsx in milkyhoop-dev/frontend/web/src
are IRRELEVANT to deploy. Mechanic correction: `git pull` does NOT restore unstaged deletions
(only checkout/restore/reset do) — moot here since deploy neither rebuilds FE nor reads that source.

## Live FE bundle is server-unique, provenance unknown (cond 2)
git diff HEAD -- frontend/ = 72 files. asset-manifest main bundle: disk=main.5558c404.js,
HEAD=main.6a02fcc0.js -> DIFFERENT builds. The deployed bundle was built/copied outside git; no
commit maps to it. FE-ORACLE SCOPE CORRECTED: proved source@master sends the 5 quote fields, NOT
that the live bundle does. ADD stands (deliberate in source). FASE-5 UI GATE: build FE from a
PINNED commit; do not trust the existing bundle.

## discount_type — my error, not a blocker (cond 3)
FE (useQuoteForm.ts:584, types default) only uses 'fixed'/'percentage' — both pass the CHECK. My
'percent' was an invented test literal. Step 1 is NOT blocked by it.
VALUE-DOMAIN DRIFT (honest): BOTH CHECK violations I hit (discount_type='percent',
vendor_credits.reason='uji') were MY OWN test literals, not application code -> ZERO confirmed code
instances. The sub-class is a hypothesis, not two findings. PROPOSAL (not now): extend the scanner
to match code string-literals written to a column against that column's CHECK/enum. ~0.5 day.

## V007 — correct reason (cond 4)
RESOLVED support = the CLEAN SCAN, not mere existence: ZERO ghost hits on unit_conversions /
item_pricing / products.base_unit in schema_scan_out.txt, AND all three exist.

## Step 1 claim DOWNGRADED (cond 4)
NOT "SQL-proven end-to-end". Proven: two INSERT statements (quotes header + quote_items) are
column-clean in psql. NOT proven: the handler, incl. the quote-number generator (never produced a
number — 0 quotes in any DB), pydantic schema, and the transaction. FASE-4 GATE: POST /quotes-with-
items -> 201 via the app.

## Sentinels — DONE
STEP0_STUB + GAP_PATCH on live schema_migrations (211 total; untracked-external; checksum=sha256 of
the creating script). migrate.sh CHECK widened + verify() skips them (verify OK, no drift).
Inventory in UNTRACKED_EXTERNAL_SCHEMA.md. build_fresh.sh-into-repo DEFERRED to post-E2E (off-path;
window stays open — FASE 4/5 use scratch clone, live stays 0-tenant).

---

# ROUND 7 — sentinel survival, served-bundle, deploy delta

## Sentinels now survive rebuild (cond 1)
(a) backfill() auto-inserts STEP0_STUB + GAP_PATCH (idempotent; checksum=sha256 of the
    creating script). No longer a manual ad-hoc INSERT that a rebuild would lose.
(b) PROVEN: fresh empty DB -> migrate.sh backfill = 194/15/209 (+2 sentinels); the
    schema_migrations CHECK on the fresh DB == live exactly (both include untracked-external).
    Bootstrap DDL and live DDL match.
(c) verify() WARNs (not fails) if a sentinel's script checksum changed -> non-decorative.
    (WARNING not DRIFT because those scripts live outside the VNNN set.)

## Served FE bundle (cond 2, read-only)
FE is served by container milkyhoop-dev-frontend-1 (3001->80) behind host nginx
(/etc/nginx/sites-enabled/milkyhoop.conf) -> Cloudflare -> milkyhoop.com. The bundle is
BAKED INTO image milkyhoop-dev-frontend (no mounts; built 2026-07-24 08:38), serving
main.5558c404.js -- DIFFERENT from HEAD's main.6a02fcc0.js. The owner's Penawaran screenshot
is from an unknown-provenance build; deploy (compose up api_gateway) never rebuilds this
image. All UI-screen inferences carry that caveat. FASE-5 UI gate: rebuild FE image from a
PINNED commit. (Corrects "compose = backend only" -> there IS a frontend container, just not
rebuilt by the api_gateway deploy.)

## Deploy delta (cond 4)
main-tree HEAD == origin/master == f71db830; api_gateway running f71db830 since 07:39; ZERO
commits on origin/master since f71db830 (no other-session drift). Deploy delta = exactly our
fix/dp-readiness commits. Round-6 rollback plan accepted + stands.

---

# CORRECTIONS (round 3) — SEVERITY UPGRADE: vendor-deposit credits the WRONG ACCOUNT CLASS

The apply journal (line 8 above) is `Dr 2-10100 (PAYABLE) / Cr 1-10800`. Re-checked
`1-10800` against the golden-path oracle (milkydb_goldenpath_green, tenant
konveksi-cemerlang): **1-10800 = "PPN Masukan" — a TAX ASSET (input VAT receivable),
account_type ASSET.** It is NOT "Uang Muka Vendor" (vendor advance / prepayment).

Consequence — the vendor-deposit write path is worse than "dead write path (500 on a
missing column)". Even if the bills.paid_amount/total_amount column bug were fixed and
the journal posted, it would be **accounting-wrong**: crediting a vendor advance settles
it against **input-VAT receivable** instead of the vendor-deposit-asset / prepayment
role. That is a class error (asset-of-the-wrong-kind), not a code-typo:
- `Cr 1-10800` reduces PPN Masukan → understates recoverable input VAT, and
- the vendor advance is never parked in its own asset account → no VENDOR_DEPOSIT_ASSET
  role resolution (Law 27) at all — it is hardcoded to a tax account.

So the vendor-deposit feature has THREE stacked defects, not one:
  1. write path 500s (bills.paid_amount/total_amount do not exist) — reachability bug;
  2. `get_bill_remaining_from_journal` copy hardcodes `2-10100` (Law 27 gap, line 116);
  3. **NEW/upgraded:** the credit leg hardcodes `1-10800` = PPN Masukan = WRONG ACCOUNT
     CLASS. Repairing (1) alone would ship a silently mis-posting feature.

Repair, when scheduled, must resolve BOTH legs by role (AP_TRADE debit +
VENDOR_DEPOSIT_ASSET/prepayment credit), never by hardcoded code. Still a conscious
DEFER (DP target flow does not touch vendor deposits), but the severity is raised from
"broken, latent" to "broken AND accounting-incorrect if naively un-broken."

---
# B4 VERDICT — CLOSED (runtime, 2026-07-26) + quote endpoint correction

## Quote-create endpoint CORRECTION (supersedes earlier "/quotes-with-items")
The FE user path for quote create is **`POST /api/quotes`** (useQuoteForm.ts:649), NOT
`/quotes-with-items` (zero FE references). The original "quotes.py POST '' is the only
create endpoint" reading was correct; `/quotes-with-items` was an error that also leaked into
the FASE-4 runbook gate (now corrected there). V219's columns ARE consumed by `POST /api/quotes`
— proven: QUO-2607-0001 persisted opening_text/closing_text/payment_* and dp_amount/dp_percent.

## B4 — does the quote's DP survive conversions? FINAL verdict
DP on a quote is explicitly **NO-LEDGER, display-only** (quotes.py detail comment "FIX_P2_QUOTEDP
... down-payment (NO-LEDGER, display only)").

- **Conversion 1 (quote -> Sales Order, step 2 `/api/quotes/{id}/to-order`): DP EVAPORATES.**
  Runtime-proven: `sales_orders` has **0** dp/deposit/down columns. The quote held dp 1.500.000 /
  30%; the SO (SO-2607-0001) carries none — it only links back via `quote_id`. There is no
  first-class DP field on the SO and thus no `required_deposit` to "confirm" (my earlier
  "confirm with required_deposit 30%" was impossible — no such column). Confirmed by column scan
  + the created SO row.
- **Conversion 2 (SO -> invoice, step 5 `/api/sales-orders/{id}/to-invoice`): pending runtime
  test at step 5** (grep says it does not carry dp; will prove there).

## CONSEQUENCE for step 4 (customer pays DP) — CLOSED decision
Because conversion 1 already dropped the DP (and step 4 precedes step 5), **nothing pre-fills the
deposit**. Step 4 must enter the DP amount MANUALLY into `POST /api/customer-deposits`
(1.500.000). This is still a valid E2E path (matches the canonical "DP @ Sales Order, apply
MANUAL" design) — it just means the harness supplies the 1.500.000 explicitly rather than reading
it from an SO field. This is the FINAL answer to B4 (revised twice before from grep alone; now
runtime-anchored for conversion 1).

---
# V219 payment_* — NEEDED BUT NOT SUFFICIENT (read/render path never built)

Runtime trace of the three V219 payment columns (payment_bank_name / payment_account_number /
payment_account_holder) across write + every read/render path:

| path | payment_* present? | evidence |
|---|---|---|
| POST /api/quotes (write) | YES — persisted | DB row: Bank BCA / 1111222233 / Kaos Biru Konveksi |
| GET /api/quotes/{id} (detail API) | **NO** — returns None | QuoteResponse never maps them (quotes.py ~420-452) |
| GET /api/quotes/{id}/pdf (customer-facing) | **NO** — never rendered | quote_data dict carries them (quotes.py ~1740) but pdf_service.generate_quote_pdf never references payment_* (grep empty) |
| PATCH /api/quotes/{id} (edit) | preserved (not destroyed) | dynamic UPDATE skips None fields (quotes.py:56 `if value is not None`); FE sends `undefined` for empty-loaded values → not overwritten |

## Conclusion
V219 was **necessary** (the columns must exist for the write to persist) but **not sufficient**:
the write path was patched while the **read/render path was never implemented**. The bank-transfer
info a customer needs (where to pay the DP) is stored and reaches **no one** — not the detail
screen, not the PDF. This must NOT be recorded as "V219 done". Remaining work: map payment_* into
QuoteResponse AND render them in pdf_service (and verify the FE detail/print surfaces them).

Edit is NOT destructive (skip-None guard + FE undefined-coalescing), so no data is lost — the data
simply sits invisible.

## Lesson (corrects our ADD-decision reasoning)
"FE sends the field → DB stores it" was our justification for ADDing these columns (V219). That
proves the WRITE path, not a working feature. **FE-sends is not evidence of a complete feature.**
A column is only "done" when write AND read/render both exist and a human can see the value. Verify
the read path (API response + PDF/print), not just the 201 on create.

---
## B4 — FULLY CLOSED (both conversions, runtime, 2026-07-26)
- **Conversion 1 (quote → SO, step 2):** DP EVAPORATES — sales_orders has 0 dp/deposit columns
  (proven step 2). SO links back only via quote_id.
- **Conversion 2 (SO → invoice, step 5):** DP EVAPORATES — sales_invoices has 0 dp/deposit columns;
  `/to-invoice` carried no dp; the Event-1 journal is Dr 1-10400 / Cr 2-10750 with no dp reference.
  The invoice links via sales_order_id (spine).
- **Net:** the quote's dp_amount/dp_percent are NEVER carried as a first-class field beyond the
  quote. The DP lives solely as the `customer_deposits` record, spine-linked (quote_id +
  sales_order_id), and is surfaced for **manual** apply at step 6 via `get_applicable_deposits`
  (a GET; there is no auto-apply). This matches the canonical "DP @ Sales Order, apply MANUAL"
  design. B4 verdict (revised twice before from grep alone) is now runtime-anchored on BOTH
  conversions and closed.

---
# STEP 6 (apply DP) — LEDGER PROVEN, user path NOT (2026-07-26)

## B0 / Branch 3 — the question that started this workstream: PROVEN
Apply 1.500.000 via POST /api/customer-deposits/{id}/apply (apply-by-ID; the FE discovery panel is
broken — see below). Separate journal DEPOSIT_APPLICATION: Dr 2-10500 1.500.000 / Cr 1-10400
1.500.000 (source_id=deposit), 2 lines, date 2026-07-09 (after invoice 07-08).
- **raw RECEIVABLE ledger = compute_ar_outstanding = 3.500.000, drift 0.** Branch 3 works: applying
  a deposit REDUCES AR; no phantom. (The scenario feared at kickoff — raw 3.5M but compute 5M — does
  NOT occur for the canonical function.)
- customer_deposit_applications: 1 row (FIRST cda in ANY DB ever), status='active', reversed_by_id
  NULL, journal_id set. uq_cda_journal_id (V218) exercised for the first time — not violated.
- 2-10500 → 0. amount_paid=1.500.000. No new bank_transaction (ledger-only). BANK_GAP=0, bank 18M.

## But the USER PATH is broken — step 6 is NOT "fully green"
- **Discovery 500:** GET /api/sales-invoices/{id}/applicable-deposits 500s (customer_id UUID vs
  VARCHAR, sales_invoices.py:3686). The FE ApplyDepositPanel lists deposits from this endpoint →
  a user cannot pick a deposit to apply. Apply is unreachable via the UI. (Filed; backend bug.)
- **Members AR overstates:** get_ar_balances_by_customer = 5.000.000 vs compute_ar 3.500.000 —
  overstated by the applied deposit (source_id join misses DEPOSIT_APPLICATION). FASE-1 [INFER] →
  confirmed fact. (Filed.)
- **Status inconsistency (minor):** sales_invoices.status stays 'posted' post-apply while
  accounts_receivable.status='PARTIAL'.

So: the LEDGER is correct and Branch 3 is proven; the customer-facing surfaces around apply are
broken. Recorded here so step 6 is not misread as end-to-end green. Two backend fixes are FASE-5
UI-gate prerequisites (see runbook).

---
# STEP 7 (Pengiriman/fulfill) — GREEN, incl. f5cd41a5 HTTP proof (2026-07-26)
Fulfilled 60 then 40 (two calls) to make the GET /fulfillments gate 3-sided AND exercise partial.
- ★ f5cd41a5 FIRST HTTP PROOF: GET /api/sales-invoices/{id}/fulfillments -> HTTP 200 (not 500),
  shippable(remaining_qty)=100 before any fulfill. The voided_reason schema-drift fix works over
  HTTP — the discriminator FASE 0.3(a) asked for. 3-sided: 100 -> 40 -> 0, each distinguishable
  from the 500/empty error (fulfillment_status pending->partial->fulfilled).
- Journals (two separate per fulfill): fulfill 60 -> COGS Dr 5-10100 2.100.000 / Cr 1-10600
  2.100.000 + Revenue Dr 2-10750 3.000.000 / Cr 4-10100 3.000.000. fulfill 40 -> COGS 1.400.000 +
  Revenue 2.000.000. Last fulfillment absorbed remainder: allocated=recognized=5.000.000,
  fulfilled_qty=100 (recognized <= allocated satisfied).
- Partial path exercised: fulfillment_status/revenue_status='partial' after 60.
- END: revenue 5.000.000, COGS 3.500.000, gross profit 1.500.000, inventory 0, stock 0,
  2-10750=0, 2-10500=0, AR stays 3.500.000, no new bank_transactions, BANK_GAP=0, bank 18M,
  drift AR/AP=0. fulfillment dates 07-12 / 07-14 (after apply 07-09, advancing).
- This validates the delivery-mode branch end-to-end: deferred at faktur -> COGS+revenue at
  Pengiriman, exactly the konveksi model the whole delivery-mode ticket is about.

---
# STEP 8 (pelunasan / final settlement) — GREEN (2026-07-27)

## FE path (confirmed by reading FE, not assumed)
Penerimaan Pembayaran -> `useReceivePaymentForm.submit` -> `useCreateReceivePayment` ->
**POST /api/receive-payments** with `save_as_draft:false`, `allocations:[{invoice_id, amount_applied}]`.
Single call — backend auto-posts (receive_payments.py:1201 `_post_payment`); NO separate `/post`
or `/confirm` in the submit flow. `bank_account_id` = bank_accounts.id (resolved to coa_id
internally at :985). This is the path exercised.

## COVERAGE GAP (logged, per owner instruction #4)
The OTHER AR-settlement path — shortcut **POST /sales-invoices/{id}/payments** — is NOT used by the
Penerimaan Pembayaran screen. Since this is the final settlement in the run, that path is **never
exercised by this harness**. Recorded as a coverage gap; would need its own scenario to cover.

## Overpayment guard (test a) — PASS
Requested 3.500.001 -> **HTTP 400 `Allocation (3500001) exceeds invoice remaining (3500000)`**.
Cap = 3.500.000 = the DEPOSIT-AWARE remaining (compute_ar_outstanding via
get_invoice_remaining_from_journal), NOT the 5.000.000 gross invoice. Guard fires at
receive_payments.py:1090-1096 BEFORE any INSERT, inside the txn -> full rollback: JE stayed 10,
BT stayed 3, receive_payments stayed 0. Zero pollution.

## Real settle (test b) — PASS
POST 3.500.000 -> HTTP 201, payment RCV-2026-0001, status=posted.
Journal (source_type RECEIVE_PAYMENT, POSTED, date 2026-07-20 = AFTER last fulfill 07-14):
- Dr 1-10201 BCA Operasional (ASSET) 3.500.000
- Cr 1-10400 Piutang (RECEIVABLE) 3.500.000

## AR fully settled — the closing target
raw RECEIVABLE ledger = compute_ar_outstanding = **0**, drift 0. AR is now zero: 1.500.000 (DP
apply, Branch 3) + 3.500.000 (this settlement) = 5.000.000 = full invoice.

## 5-artifact contract (ARAP Rule 1) — all present
1. journal_entries: 1 RECEIVE_PAYMENT, status POSTED.
2. receive_payments: 1 row, status='posted', total 3.500.000, date 07-20.
3. receive_payment_allocations: 1 row — applied 3.500.000, **remaining_before 3.500.000**
   (P35_ARCANON deposit-aware proof), remaining_after 0.
4. bank_transactions: 4 total (was 3); newest +3.500.000 POSTED 07-20.
5. sales_invoices cache: amount_paid=5.000.000, status=**paid** (recomputed from
   compute_ar_outstanding at :1738, not a bespoke counter).

## Bank + counts
1-10201 ledger balance = **21.500.000** (20M opening − 3.5M bill pay + 1.5M DP + 3.5M settle;
**delta from opening +1.500.000** = the gross profit, in delta terms). BANK_GAP=0. journal_entries
10->11, bank_transactions 3->4. DRIFT AR=0 / AP=0. BANK_GAP compute==ledger==21.500.000.

## Minor observation (not a ledger defect, not filed as its own ticket)
The create RESPONSE returned `accounting_status: null` while receive_payments.status='posted' and
the journal is POSTED in the DB. Response-serialization quirk on the create envelope only — the DB
is correct (status POSTED). LOW; noted for whoever touches the response mapper.

---
# STEP 9 (tutup SO) + FASE 6 (closing invariant) — GREEN (2026-07-27)

## CORRECTION to STEP 8 note — accounting_status symmetry HOLDS (item 1)
Step-8 wording ("DB is POSTED while response is null") was imprecise and I withdraw it. Actual DB:
`receive_payments.accounting_status='UNPOSTED'` AND `bill_payments_v2.accounting_status='UNPOSTED'`
— both at the column DEFAULT ('UNPOSTED'), neither post path writes it. What is POSTED is
`journal_entries.status`; both wrappers also carry `status='posted'`. So AR and AP behave
IDENTICALLY — the benign-symmetric classification stands. The create response's `accounting_status:
null` is just the response mapper not selecting that column; the ledger source of truth is correct.

## STEP 9 — tutup SO (POST /api/sales-orders/{id}/close), FE-confirmed
FE SalesOrderDetailDesktop.tsx:205 -> POST /api/sales-orders/{id}/close ("Pesanan ditutup").
Backend sales_orders.py:950: precondition status IN ('invoiced','shipped'); sets 'completed'; NO
ledger. Result: HTTP 200, status invoiced->completed. **ZERO new journal (11->11)**, no bank
movement (4->4). fiscal period 2026-07 = **OPEN** (period NOT closed — deliberately; closing would
lock July, Law 5). drift AR/AP=0, BANK_GAP=0.

### ★ Runtime proof for audit finding #4 (independent counters, no cross-reconciliation)
Pre- and post-close: `sales_orders.shipped_qty = 0` despite 100 pcs fully delivered, while
`invoiced_qty = 100`. Fulfillment ran via `sales-invoices/{id}/fulfill` (updates invoice, not SO);
`/to-invoice` filled invoiced_qty but nothing fills shipped_qty, and `/close` does not reconcile it.
Was [CODE]-only; now RUNTIME-CONFIRMED. Product consequence: the Pesanan page shows an order as
"belum dikirim / shipped 0" when its goods have entirely left the warehouse. Upgrade the ticket.

## B — non-owner permission probe: BLOCKED (see 2026-07-27-team-invite-broken-no-loggable-user.md)
No API path to provision a loggable low-role user: invite 400 "Invalid role_id" (tenant-scoped role
lookup vs global `__SYSTEM__` roles) AND no `team_invitations` writer anywhere (accept/set-password
orphaned). Dimension D (role-based 403) is NOT validated this session; stated openly, not skipped.

## FASE 6 — CLOSING INVARIANT (closing_invariant.sql, run as-is) — ALL PASS
| metric | expected | actual | result |
|---|---|---|---|
| AR_TRADE (1-10400) | 0 | 0 | PASS |
| AP_TRADE (2-10100) | 0 | 0 | PASS |
| CUSTOMER_DEPOSIT_LIABILITY (2-10500) | 0 | 0 | PASS |
| REVENUE_DEFERRED (2-10750) | 0 | 0 | PASS |
| INVENTORY_MERCHANDISE (1-10600) | 0 | 0 | PASS |
| COGS_SALES (5-10100) | 3.500.000 | 3.500.000 | PASS |
| REVENUE_SALES_GOODS (4-10100) | -5.000.000 | -5.000.000 | PASS |
| BANK_DELTA (excl opening 20M) | +1.500.000 | +1.500.000 (net 21.5M) | PASS |
| GROSS_PROFIT (rev 5M - cogs 3.5M) | 1.500.000 | 1.500.000 | PASS |
| TRIAL_BALANCE (gross line-sum Dr=Cr) | balanced | 47.000.000 = 47.000.000 | PASS |
| TRIAL_BALANCE (net account balances Dr=Cr) | 25M=25M | 25.000.000 = 25.000.000 | PASS |
| AR_OUTSTANDING (compute_ar) | 0 | 0 | PASS |
| AP_OUTSTANDING (compute_ap) | 0 | 0 | PASS |
| VAT_LINES (non-PKP) | 0 | 0 | PASS |
| HASH_CHAIN breaks | 0 | 0 | PASS |
| JE count | 11 | 11 | PASS |

Note: the SQL's TRIAL_BALANCE row sums line-level debits/credits (gross, 47M) proving every journal
balances; the net-account version (25M=25M: Dr bank 21.5M + COGS 3.5M ; Cr equity 20M + revenue 5M)
was computed separately and also balances. No FAIL, no rounding, no tolerated delta.

## Remaining (needs separate GO): D — clean single-shot run (run_all.sh from preharness restore).

---
# FASE 5 — CLEAN SINGLE-SHOT RUN (run_all.sh) — PASS (2026-07-27)

## Result: the harness is a reproducible regression suite, not 9 scripts that once worked.
Restore preharness → verify pristine → `bash run_all.sh` → ran -1→0→0b→1→2→[3 SKIP]→4→5→6→7→8→9
→ closing invariant, ONE shot, NO intervention. **ALL steps PASS, closing invariant clean.**

## Timing (total 24s)
-1=4s, 0=1s, 0b=1s, 1=2s, 2=1s, 4=2s, 5=3s, 6=2s, 7=3s, 8=4s, 9=1s. Total 24s.

## Reproducibility proof (fresh from pristine, identical to the staged run)
- Sequences reset correctly (RISK 2): QUO-2607-0001, SO-2607-0001, INV-2607-0001,
  PAY-202607-0001, RCV-2026-0001, dp_percent 30.0. No number leaked outside the snapshot.
- journal_entries = 11.
- Closing invariant numbers byte-identical to the earlier run: COGS 3.500.000, revenue
  -5.000.000, CUSTOMER_DEPOSIT_LIABILITY/REVENUE_DEFERRED/INVENTORY/AR/AP = 0, BANK_DELTA
  +1.500.000 (net 21.5M), GROSS_PROFIT 1.500.000, TRIAL_BALANCE 47.000.000=47.000.000,
  AR/AP outstanding 0, VAT 0, HASH_CHAIN 0 breaks. drift AR/AP=0 after every step.

## How many restores/reruns before clean: 3 runs, 3 restores. Both failures were in the RUNNER,
never the flow (the flow logic passed unchanged throughout):
1. `local label=$1 script=$2 log="...${label}..."` — bash expands ALL `local` argument words
   BEFORE assigning, so `${label}` was read while still unset → `set -u` abort. Fixed: split the
   `local` into two statements.
2. FAIL-token false positive — the detector matched the word "FAIL" inside step 5's DESCRIPTIVE
   echo `"(expect +1 ... +2/+3 => auto-fulfill FAIL)"`. Fixed: match only genuine verdicts
   (`FAIL` followed by EOL / whitespace / "("), never `FAIL)`; audited all child scripts to
   confirm the only always-printed FAIL token is that one descriptive line.

## Risks checked up front (not discovered by surprise)
- EMAIL/pending_registrations: preharness snapshot has pending_registrations=0 → signup register
  for owner@kaosbiru.co.id does not conflict. (restore_preharness.sh asserts it.)
- SEQUENCES: verified reset to -0001 above.
- state.env: run_all.sh `rm`s it at start; step_-1 bootstraps config from env defaults (does not
  source state.env), so no stale IDs.
- RACE: all posting (bill/invoice/fulfill/receive-payment/deposit) is synchronous in-request
  (journal POSTED before the response). Steps run back-to-back with no sleeps and no read saw stale
  state — 24s end to end, zero flakiness.

## Deliverables committed
- scripts/e2e/dp_flow/run_all.sh — the single-shot runner (per-step drift, first-failure stop,
  timing). Failure gate is explicit (rc!=0 OR verdict token) rather than bare `set -e`, because the
  child scripts signal logical failures as printed text, not exit codes.
- scripts/e2e/dp_flow/restore_preharness.sh — restore + pristine verification (Tenant/mig/pending/
  User/JE).

---
# NEGATIVE TESTS + rc-HARDENING + MISSION CLOSE (2026-07-27)

## Detection mechanism changed: rc-PRIMARY (was string-matching only)
Owner directive: token matching is a silent-fallback risk — a real failure whose format drifts
would pass silently. Now every child EXITS NON-ZERO on failure:
- `verdict.sh` (new, shared): step scripts assert via aeq/ane/atrue; `finish()` exits 1 if any
  assertion failed. All 11 steps retrofitted (step_0/0b only printed before — now assert
  grand_total, journal sides, remaining_before, bank deltas, etc.).
- `drift_check.sql` + `closing_invariant.sql`: end in a division-by-zero rc-gate under
  `ON_ERROR_STOP` → psql exits 3 on ANY drift/invariant failure. Denominator depends on a column
  so Postgres can't constant-fold it away when the count is 0.
- `run_all.sh`: gates each step AND its post-step drift on EXIT CODE first (captures PIPESTATUS);
  the token scan is now only a safety belt. Clean hardened run stayed GREEN (29–30s, all children
  exit 0, closing_rc_gate=1).

## NEGATIVE TESTS — the suite is proven as a DETECTOR (restore between each, injection reverted after)
A suite that only ever passes is not proven to catch anything. Injected one fault at a time and
confirmed run_all STOPS at the right step, via rc, with a clear message:
| # | injection | expected stop | result |
|---|-----------|---------------|--------|
| (a) | step_0 grand_total expected 3500000→3500001 | step 0 | STOP at STEP 0, child exit 1, "FAIL — bill grand_total: got '3500000' want '3500001'" ✓ |
| (b) | drift_check.sql AR ledger +1 (printed CTE + rc-gate) | first drift | STOP at "step -1-drift", rc=3 (division by zero), printed `AR 1|0|1 FAIL` ✓ |
| (c) | closing_invariant.sql GROSS_PROFIT expected 1500000→1500001 (display + gate) | closing | STOP at closing, GROSS_PROFIT display FAIL, gate rc=3 ✓ |
Each caught via the PRIMARY rc path (not just the token belt). After reverting all three: final
clean run GREEN, run_all exit 0 — zero corruption left behind.

## CI OPPORTUNITY (proposal — NOT implemented, owner decision)
The full flow runs in ~30s from pristine → viable as a per-deploy CI gate rather than a manual
occasional check. Paired with the schema-contract "ghost column" CI ratchet, that is two nets:
one catches SCHEMA drift, one catches a LEDGER that does not close.
Prerequisites to wire it: (1) a scratch Postgres in CI seeded from the preharness snapshot (or a
migrations-only build) — never the live DB; (2) the api_gateway reachable from the CI job
(compose up, or a service container) + the signup magic-link path enabled; (3) CI secret for DB
superuser (restore) — kept out of logs; (4) a stable tenant slug per job to avoid cross-run
collisions; (5) budget the restore (~20s) + run (~30s) ≈ 1 min/gate. Suggested trigger: on PRs
touching backend/ (accounting, banksync, inventory, sales) + nightly.

## ══════════ MISSION CLOSE — DP cash-to-cash ledger ══════════
### PROVEN
- The DP cash-to-cash LEDGER is correct AND reproducible from zero (run_all.sh, single shot,
  rc-gated, negative-tested): buy→pay→quote→SO→DP→faktur(deferred)→apply→ship→settle→close.
- Branch 3 (applying a DP REDUCES AR, no phantom): raw RECEIVABLE == compute_ar throughout.
- Law 29/30 (DP is a LIABILITY, never touches RECEIVABLE): zero RECEIVABLE lines in the DP journal.
- f5cd41a5 (GET /fulfillments schema-drift) fixed and proven over HTTP (200, 3-sided gate).
- B4 closed (DP evaporates at quote→SO conversion — no destination column; received at SO instead).
- Audit finding #4 upgraded [CODE]→RUNTIME: sales_orders.shipped_qty stays 0 after full delivery.
- Closing invariant ALL PASS, numbers identical run-to-run: trial balance 25M=25M (net) / 47M=47M
  (gross), gross profit 1.5M, hash chain intact, drift AR/AP=0, bank delta +1.5M.

### NOT PROVEN — the USER-FACING PATH
The ledger engine is proven; the product surfaces that let a user DRIVE it are not. Five things a
real user CANNOT do today (three require raw DB writes, which is how the harness had to do them):
1. Create a non-PKP tenant — no API to set Tenant.is_pkp (raw UPDATE). [issue: no-api-for-tenant-is_pkp]
2. Set delivery-mode revenue recognition — no API for tenant_config.revenue_recognition_policy (raw INSERT). [delivery-mode-...unreachable]
3. See applicable deposits to apply — GET /applicable-deposits 500s (UUID vs VARCHAR). [applicable-deposits-500]
4. "Tagih DP" — no DP-billing endpoint exists (documented gap; DP is received straight off the SO).
5. Invite a team member who can log in — invite 400 "Invalid role_id" (tenant-scoped vs __SYSTEM__
   roles) + no team_invitations writer. [team-invite-broken-no-loggable-user]
AND: permission dimension D (role-based 403 enforcement) is UNVALIDATED — there is no way to create
a non-owner user, so this run proves NOTHING about permission gating.

### One-line verdict
**The ledger engine is proven correct; the product surface that would let a user move it is not.**
Batch fix awaits owner decision on ordering.

---
# BATCH FIX #1 — DP path reachable via UI (2026-07-27)

## Owner correction adopted: "tagih DP" was a MIS-CLASSIFICATION
PENAWARAN (quote) IS the DP billing instrument in Indonesian UMKM practice — it already carries
dp_amount/dp_percent + payment_bank_name/account_number/account_holder, and the customer transfers
against it. So the long-standing "tagih DP absent" note was WRONG: the feature EXISTS; only the
RENDER was incomplete. No new document needed. Harness step 3 message reframed accordingly; the
applicable-deposits + quote-detail tickets reframed/closed (below).

## ITEM A — applicable-deposits 500 → FIXED
- A1: `sales_invoices.py` get_applicable_deposits bound `invoice["customer_id"]` (a UUID from
  sales_invoices) to the VARCHAR `customer_deposits.customer_id` → asyncpg "expected str, got UUID"
  → 500. Fix: bind `str(invoice["customer_id"])` (no column change). Endpoint now 200.
- A2: audited the whole class (see DOCS/issues/2026-07-27-uuid-varchar-bind-class-audit.md).
  asyncpg only breaks UUID→VARCHAR, and the ONLY VARCHAR *_id column is customer_deposits.
  customer_id (lone drift; every sibling is uuid). One live site (A1) — fixed. All others already
  defensive/safe. One DEAD helper (compute_customer_deposit_balance, no callers) flagged, not fixed.

## ITEM B — quote payment_* / dp_* rendering
- B1 (FIXED): `get_quote_detail` never mapped payment_bank_name/_account_number/_account_holder into
  QuoteDetail (the model already declared them) → GET /api/quotes/{id} returned None. Now mapped.
- B2/B3 (ALREADY DONE — earlier finding was stale): `quote.html` ALREADY renders the Rekening
  Pembayaran block AND the Uang Muka / Sisa Pembayaran block (FIX_P2_QUOTEDP 2026-06-16), and the
  PDF handler's quote_data already carries payment_* + dp_*. Verified empirically: GET /pdf → 200,
  real %PDF, and the DB row it renders carries bank + account + dp. Template UNTOUCHED (per pdf skill).

## ITEM C — harness gates (so neither bug can regress silently)
- C1: step 6 applicable-deposits is now a REAL assertion (HTTP 200 + our deposit present + available
  1.500.000 + suggested_amount 1.500.000), not a KNOWN-500 pass-through.
- C2: step 1 read-back — GET /api/quotes/{id} returns all 5 V219 columns NON-NULL
  (opening_text, closing_text, payment_bank_name/_account_number/_account_holder).
- C3: step 1 PDF — GET /api/quotes/{id}/pdf → 200 + %PDF magic; PDF binary text is FlateDecode-
  compressed so (per owner) the render CONTENT is asserted from the supplying DB row (bank + account
  + dp) rather than the binary — stated plainly, not pretended.

## VERIFICATION (D) — tested on an ISOLATED gateway, live master untouched
The dev gateway bind-mounts /root/milkyhoop-dev (master, read-only, no reload), so to test the
worktree fix WITHOUT deploying to live I ran a second api_gateway container (`mh-test-gw`, port
8002) bind-mounting the worktree, same network + milkydb (disposable, no real users). Then:
- D1/D2: restore preharness → run_all single-shot vs 8002 → GREEN (28s, all children exit 0). New
  gates C1/C2/C3 PASS. Closing invariant ALL PASS with numbers IDENTICAL to the prior run (COGS
  3.5M, revenue -5M, BANK_DELTA +1.5M, gross profit 1.5M, trial balance 47M=47M, AR/AP 0, VAT 0,
  hash chain 0). Nothing shifted → the fix touched only what it should.
- D3: broke C1's expected (1500000→1500001) → run STOPPED at step 6 (rc=1, clear message) → reverted
  → final clean run GREEN. New gate proven to catch.
`mh-test-gw` is a throwaway test rig, torn down after verification.

---
# BATCH FIX #1 — CORRECTIONS (2026-07-27, post owner review)

## B2/B3 now GENUINELY PROVEN (was overclaimed)
Earlier I wrote "verified empirically" while C3 only checked 200 + %PDF + the DB supplying data —
that does NOT prove the PDF displays the values (a `payment_bank_name` vs `bank_name` template
mismatch would render BLANK with no error — silent-fallback class). Corrected: extracted the
rendered PDF text with pdfminer.six (WeasyPrint embeds SUBSETTED fonts → glyph IDs, so zlib/grep on
raw bytes finds NOTHING and would falsely pass — verified that failure mode too). The extracted
text DOES contain: `Bank BCA`, `1111222233`, `a.n. Kaos Biru Konveksi`, `Uang Muka (30%)`,
`Rp 1.500.000`, `Sisa Pembayaran Rp 3.500.000`. So B2/B3 = PROVEN. No var-name mismatch; template
untouched.

## C3 rewritten to assert on REAL extracted text
Harness step 1 C3 now runs `scripts/e2e/pdf_text.sh` (pdfminer) on the returned PDF and asserts the
rendered text CONTAINS 1111222233 + Bank BCA + 1.500.000 + Uang Muka — replacing the weaker
supplying-data check. New helper `acontains` in verdict.sh. Full suite re-run GREEN (32s), C1/C2/C3
all PASS, closing invariant identical.

## A2 audit overclaim corrected (information_schema, not grep)
"the only VARCHAR *_id column" was WRONG. Corrected via information_schema: the accurate class is
"VARCHAR columns referencing a uuid PK" — TWO exist, both → customers.id (uuid):
`customer_deposits.customer_id` AND `credit_notes.customer_id`. (tenant_id is VARCHAR but
"Tenant".id is `text` = consistent; tax_id is not an FK.) The second site is a LIVE bug
(credit_notes.py:256 binds UUID→VARCHAR on the by-customer list filter) — FILED
(2026-07-27-credit-notes-customer-id-uuid-varchar.md), out of BATCH1 scope. This recurring class
strengthens the column-conversion decision (2026-07-27-DECISION-customer-id-varchar-to-uuid.md).

## Test-gateway pattern made permanent + guarded
`scripts/e2e/test_gateway.sh` (up/down) + `scripts/e2e/pdf_text.sh`. HAZARD documented (writes to
LIVE milkydb — safe only pre-launch); GUARD refuses to start if milkydb has >1 tenant or any tenant
outside the harness slug. Runbook: 2026-07-27-batch2-deploy-runbook.md. (Also to be added to the
milkyhoop-e2e skill.)

## E1/E2 owner decisions recorded
E1: DEPLOY NOW, do not batch with team-invite (blast radius; cheapest window is now). E2: deploy →
restore pristine → run step -1 ONLY (tenant + master data, zero transactions) as the UI-walkthrough
basis. Both in the runbook. Deploy awaits explicit GO.
