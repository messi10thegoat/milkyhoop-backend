# Hygiene debt: FE branch feat/uang-muka-rest tracks node_modules

**Filed:** 2026-06-16 (alongside P3 bridge)
**Severity:** Hygiene / repo cleanliness, non-blocking
**Area:** Frontend repo (origin/main repo), branch `feat/uang-muka-rest` (P1 FE base)

## Symptom
The FE branch `feat/uang-muka-rest` (the P1 frontend base for the Uang Muka /
DP work) has **node_modules committed** into git -- roughly 6675 partial files
tracked. `node_modules` should be gitignored, not version-controlled.

## Impact
- Bloated diffs / clones, noisy `git status`, slow operations.
- Risk of committing machine-specific or platform-specific build artifacts.
- Merge conflicts in vendored dependency files.

## Root cause
`node_modules` is not (or not effectively) listed in `.gitignore` for that
branch, or the files were `git add`-ed before the ignore rule existed.

## Proposed cleanup
1. Ensure `node_modules/` is in the FE `.gitignore`.
2. `git rm -r --cached node_modules` on the branch, commit the removal.
3. Verify `git status` is clean and a fresh `npm install` reproduces the tree.

## Notes
- Pre-existing condition; not introduced by P3. Flagged here so it is not lost.
- Belongs to the FE repo (origin/main), separate from this backend (master)
  commit; tracked here only because P3 surfaced it.
