# Git Backup SOP - Milkyhoop

## Quick Reference

### One-Liner Backup (Copy-Paste Ready)
```bash
export TZ="Asia/Jakarta" && TIMESTAMP=$(date +"%Y-%m-%d %H:%M WIB") && \
cd /root/milkyhoop && git add -A && (git diff --cached --quiet || git commit -m "backup: frontend - $TIMESTAMP") && \
cd /root/milkyhoop-dev && git add -A && (git diff --cached --quiet || git commit --no-verify -m "backup: backend - $TIMESTAMP") && \
echo "✅ Backup complete: $TIMESTAMP"
```

### Quick Status Check
```bash
echo "FRONTEND:" && git -C /root/milkyhoop status -s && \
echo "BACKEND:" && git -C /root/milkyhoop-dev status -s
```

---

## Environment Structure

| Directory | Environment | Contents |
|-----------|-------------|----------|
| `/root/milkyhoop` | FRONTEND | React app, Nginx, frontend docker |
| `/root/milkyhoop-dev` | BACKEND | API Gateway, Services, Backend docker |

### Rules
- **NEVER** commit backend code to `/root/milkyhoop`
- **NEVER** commit frontend code to `/root/milkyhoop-dev`
- **NEVER** mix docker-compose between environments

---

## Backup Procedures

### Standard Backup

```bash
# Step 1: Set timezone
export TZ="Asia/Jakarta"
TIMESTAMP=$(date +"%Y-%m-%d %H:%M WIB")

# Step 2: Frontend
cd /root/milkyhoop
git add -A
git diff --cached --quiet || git commit -m "backup: frontend - $TIMESTAMP"

# Step 3: Backend (skip slow pre-commit hooks)
cd /root/milkyhoop-dev
git add -A
git diff --cached --quiet || git commit --no-verify -m "backup: backend - $TIMESTAMP"

# Step 4: Verify
echo "=== FRONTEND ===" && git -C /root/milkyhoop log -1 --oneline
echo "=== BACKEND ===" && git -C /root/milkyhoop-dev log -1 --oneline
```

### Backup with Custom Message
```bash
export TZ="Asia/Jakarta" && TIMESTAMP=$(date +"%Y-%m-%d %H:%M WIB") && \
MSG="[your message here]" && \
cd /root/milkyhoop && git add -A && (git diff --cached --quiet || git commit -m "backup: frontend - $TIMESTAMP $MSG") && \
cd /root/milkyhoop-dev && git add -A && (git diff --cached --quiet || git commit --no-verify -m "backup: backend - $TIMESTAMP $MSG") && \
echo "✅ Backup complete: $TIMESTAMP $MSG"
```

---

## Safety Audit

### Pre-Backup Audit
Run this BEFORE important work sessions to ensure clean state:

```bash
echo "╔═══════════════════════════════════════════╗"
echo "║         GIT SAFETY AUDIT                  ║"
echo "╚═══════════════════════════════════════════╝"

# Check Frontend
echo -e "\n=== FRONTEND ==="
cd /root/milkyhoop
git log -1 --format="Last: %h - %s (%cr)"
STATUS=$(git status -s)
[ -z "$STATUS" ] && echo "Status: ✅ CLEAN" || echo -e "Status: ⚠️ DIRTY\n$STATUS"

# Check Backend
echo -e "\n=== BACKEND ==="
cd /root/milkyhoop-dev
git log -1 --format="Last: %h - %s (%cr)"
STATUS=$(git status -s)
[ -z "$STATUS" ] && echo "Status: ✅ CLEAN" || echo -e "Status: ⚠️ DIRTY\n$STATUS"

# Check for nested .git (problematic)
echo -e "\n=== NESTED .GIT CHECK ==="
NESTED_FE=$(find /root/milkyhoop -name ".git" -type d 2>/dev/null | grep -v "^/root/milkyhoop/.git$")
NESTED_BE=$(find /root/milkyhoop-dev -name ".git" -type d 2>/dev/null | grep -v "^/root/milkyhoop-dev/.git$")
[ -z "$NESTED_FE" ] && echo "Frontend: ✅ No nested .git" || echo -e "Frontend: ⚠️ FOUND:\n$NESTED_FE"
[ -z "$NESTED_BE" ] && echo "Backend: ✅ No nested .git" || echo -e "Backend: ⚠️ FOUND:\n$NESTED_BE"
```

### Fix Nested .git Issues
If audit finds nested `.git` directories:

```bash
# 1. Commit changes inside nested repo first
cd /path/to/nested/repo
git add -A && git commit -m "final state before merge"

# 2. Remove nested .git
rm -rf .git

# 3. Add to parent repo
cd /root/milkyhoop-dev  # or /root/milkyhoop
git rm --cached path/to/nested/repo  # remove gitlink
git add path/to/nested/repo          # add as regular files
git commit -m "merge: nested repo into parent"
```

---

## Automated Backup (Cron)

### Setup Daily Backup at 23:59 WIB
```bash
crontab -e
```

Add this line:
```cron
59 16 * * * export TZ="Asia/Jakarta" && T=$(date +"\%Y-\%m-\%d \%H:\%M WIB") && cd /root/milkyhoop && git add -A && (git diff --cached --quiet || git commit -m "backup: frontend - $T") 2>/dev/null; cd /root/milkyhoop-dev && git add -A && (git diff --cached --quiet || git commit --no-verify -m "backup: backend - $T") 2>/dev/null
```

> Note: `59 16 * * *` = 16:59 UTC = 23:59 WIB (UTC+7)

---

## Recovery

### Restore to Last Commit
```bash
# Frontend
cd /root/milkyhoop && git checkout .

# Backend
cd /root/milkyhoop-dev && git checkout .
```

### Restore to Specific Commit
```bash
# Find commit hash
git log --oneline -20

# Restore (creates new commit, safe)
git revert --no-commit HEAD~3..HEAD  # revert last 3 commits
git commit -m "revert: rollback to [hash]"

# OR hard reset (destructive, use with caution)
git reset --hard <commit-hash>
```

### View What Changed Since Last Backup
```bash
# Frontend
git -C /root/milkyhoop diff HEAD

# Backend
git -C /root/milkyhoop-dev diff HEAD
```

---

## .gitignore Essentials

These patterns are already configured to prevent tracking generated files:

```gitignore
# Node
**/node_modules/.cache/
**/.eslintcache

# TypeScript
**/tsconfig.tsbuildinfo

# Prisma
**/.prisma/client/

# Backups (local only)
backups/*.sql.gz.age
*.backup*
```

---

## Troubleshooting

### "Nothing to commit" but status shows files
Usually means gitlinks (submodule-like). Fix:
```bash
git rm --cached <path>  # remove gitlink
git add <path>          # re-add as regular files
```

### Pre-commit hook takes too long
Use `--no-verify` flag:
```bash
git commit --no-verify -m "message"
```

### Modified submodule with 0 changes
Indicates nested `.git` directory. See "Fix Nested .git Issues" above.

---

## Version History

| Date | Version | Changes |
|------|---------|---------|
| 2026-01-14 | v3.0 | Added nested .git handling, audit procedures |
| 2026-01-12 | v2.0 | Added --no-verify for backend, auto-format handling |
| 2026-01-10 | v1.0 | Initial SOP |
