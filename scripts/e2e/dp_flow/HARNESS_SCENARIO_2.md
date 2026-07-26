# Harness Scenario #2 (BACKLOG — do not run yet): DEFAULT auto-fulfill path

## Why this exists
The primary DP harness deliberately tests the NON-default path (delivery mode, revenue deferred to
Pengiriman) because that is the konveksi spec. But **100% of real tenants today get the DEFAULT**:
`revenue_recognition_policy='invoice'` → sell-from-stock **auto-fulfills** (Event 1+2+3 atomically at
invoice post). That path deserves its own end-to-end run so the default a real user actually hits is
also proven closed.

## What scenario #2 would do
Same setup (Kaos Biru, buy 100 @ 35.000, sell 100 @ 50.000, non-PKP, DP 30%), but tenant left at
the default `'invoice'` policy (skip the tenant_config delivery flip).

- Step 5 (faktur post): expect **3 journals** in one shot —
  - Event 1: Dr 1-10400 AR 5.000.000 / Cr revenue (or deferred then immediately recognized)
  - Event 2: Dr 5-10100 COGS 3.500.000 / Cr 1-10600 Persediaan 3.500.000
  - Event 3: revenue recognition Dr 2-10750 / Cr 4-10100 5.000.000 (net: Dr AR / Cr 4-10100 + COGS)
  - fulfillment_status=fulfilled, revenue_status=recognized, stock → 0 at faktur.
- Step 7 (Pengiriman): SKIPPED / not-applicable (nothing left to ship).
- The closing invariant must STILL close (AR settles, inventory 0, gross profit 1.500.000, bank
  delta +1.500.000). The DP apply (step 6) and settlement (step 8) still run.

## Value
- Proves the path every real tenant walks.
- Confirms the closing invariant is path-independent (delivery vs invoice both close to the same
  end state), which is a strong correctness signal.
- Contrast run: any invariant that closes in ONE mode but not the other localizes a real bug.

Deferred until the primary (delivery-mode) scenario reaches step 9 green.
