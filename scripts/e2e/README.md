# Golden Path E2E — full accounting cycle gate

Self-contained driver that validates the **fresh-install recipe** through a
complete accounting cycle over real HTTP (gateway :8001). Proven green
2026-07-25 on a DB built by the hardened runner (194 OK / 0 FAIL / 15 SKIP).

## Files
- `goldenpath.sh` — signup → master data → bill+payment → WO (material/labor/OH)
  → FG receipt (WIP net 0, FG WAC 71.500) → payroll (BPJS multi-line) → sales
  invoice PSAK-72 3-event + PPN → pelunasan → expense → bank transfer (BANK_FEE
  6.500, V198) → manual JV → mfg reconcile → period close + lock test.
  Creates tenant `konveksi-cemerlang`.
- `goldenpath_invariants.sql` — the 12-invariant gate (TB balanced, Law-4=0,
  WIP/Deferred net 0, applied labor/OH 0, AR/AP GL==compute_*, chain integrity,
  bank==GL, inventory GL==ledger).

## Run (against a throwaway DB, never live)
Build a fresh DB with the hardened runner, point the whole stack at it
(rename-promote: stop app containers → `ALTER DATABASE ... RENAME` → start), then:
```bash
bash goldenpath.sh
docker exec -i milkyhoop-dev-postgres-1 psql -U postgres -d milkydb -f goldenpath_invariants.sql
```
The gateway must connect to the DB under test — the golden path writes via HTTP,
so a stack still pointed at live `milkydb` would measure the wrong DB.

## Expected key values
FG WAC 71.500/pcs · PPN in 495.000 / out 280.500 · BANK_FEE 6.500 ·
payroll BPJS EE 360.000 + ER 948.600 · period CLOSED + backdate JV rejected (Law 5) ·
chain integrity all valid · AR=AP=0 after settlement.
