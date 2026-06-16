# Ticket / RULE: Migration numbers collide across parallel sessions on shared milkydb

- Filed: 2026-06-16
- Severity: process rule (recurring, same class as the git-push race)
- Origin: P2 Quote→DP (FIX_P2_QUOTEDP) migration renumber step

## Observed collisions
On the shared backend repo, migration files use a `VNNN__name.sql` convention
applied MANUALLY against the shared `milkydb`. Numbers have collided when
parallel sessions each picked "the next number" off a STALE local tree:

- `V177__backfill_orphan_journal_number_sequences.sql`  AND
  `V177__deposit_application_reversal.sql`   <- TWO different V177 on origin/master.
- V178 was at risk of the same fate during P2.

## Why this is dangerous
1. Two files share a number -> ambiguous "what ran" + merge noise.
2. If a tracking table were ever introduced, a stale tracking row for `VNNN`
   could shadow a DIFFERENT parallel `VNNN`, causing a runner to skip it.
   (Mitigating fact today: there is currently NO tracking table for these
   `VNNN` migrations — see "Current mechanism" below — and all P2 ALTERs are
   idempotent `IF NOT EXISTS`, so the schema is safe regardless. The risk is
   future, when/if a runner+tracking table lands.)

## Current mechanism (verified 2026-06-16)
- NO migration runner script exists in the repo.
- NO tracking table tracks `VNNN__*.sql`. `_prisma_migrations` is EMPTY (leftover
  Prisma artifact); `fle_migration_status` tracks field-level-encryption DATA
  migrations (table/field rows), not schema `VNNN` files.
- `VNNN__*.sql` are applied BY HAND against milkydb. So a renumber is FILE-ONLY;
  there is no DB tracking row to realign and no stale row to remove.

## RULE (durable)
Before creating OR applying a migration:
1. `git fetch origin` FIRST, then determine the next-free `VNNN` from
   origin/master AND all active branches:
   `git ls-tree -r --name-only origin/master backend/migrations/ | grep -oE 'V[0-9]+' | sort -tV -k2 -n | uniq | tail`
   (and scan remote branches if parallel work is suspected).
2. Create/apply the migration ONLY from a POST-FETCH worktree, so the chosen
   number reflects the latest remote state — never off a stale tree.
3. Treat the number as RESERVED the moment you apply it; push the file promptly
   so other sessions see it. This is the same discipline as serializing pushes
   to avoid the git race.
4. If a runner + tracking table is ever added, the renumber procedure MUST also
   move/rename the tracking row to the final number and delete any stale row,
   so a parallel `VNNN` can still run.
