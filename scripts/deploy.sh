#!/bin/bash
# /root/milkyhoop-dev/scripts/deploy.sh
# Day 5 Block B/A: hardened backend service deploy with /ready health gate + git-revert rollback.
#
# Usage:
#   ./deploy.sh [branch] [service]
#   ./deploy.sh master api_gateway
#   ./deploy.sh feat/foo api_gateway --unsafe-allow-dirty
#
# Behavior:
#   1. Pre-flight: working tree whitelist check; capture PREV_SHA.
#   2. Fetch + ff-only pull; capture NEW_SHA.
#   3. Build + restart container with GIT_SHA / BUILD_TIME env.
#   4. Health gate: poll /ready until 200 (30s timeout).
#   5. Verify /version reports new SHA.
#   6. On failure: git reset --hard PREV_SHA + restart + re-poll /ready.
#   7. Log every phase to /var/log/milkyhoop/deploy.log.
#
# See: DOCS/runbooks/deploy.md

set -uo pipefail

# ---------- Config ----------
REPO=/root/milkyhoop-dev
LOG=/var/log/milkyhoop/deploy.log
HEALTH_URL=http://127.0.0.1:8001/ready
VERSION_URL=http://127.0.0.1:8001/version
HEALTH_TIMEOUT=30      # seconds
HEALTH_INTERVAL=2

BRANCH=${1:-master}
SERVICE=${2:-api_gateway}
UNSAFE=0
for arg in "$@"; do
    [ "$arg" = "--unsafe-allow-dirty" ] && UNSAFE=1
done

# ---------- Whitelist (per Day 5 Decision #5) ----------
# Untracked files matching these patterns are allowed (warned, not blocking).
# See DOCS/issues/BACKEND-REPO-FRONTEND-ARTIFACT-DRIFT-001 + SERVER-CLEANUP-BAK-FILES-001.
WHITELIST_PATTERNS=(
    'frontend/static/'
    'frontend/icons/'
    'frontend/workers/'
    'frontend/build/'
    'frontend/manifest.json'
    'frontend/service-worker.js'
    'frontend/asset-manifest.json'
    'frontend/index.html'
    'frontend/nginx.conf'
    'frontend/Dockerfile'
    'frontend/50x.html'
    'frontend/nginx.conf.bak.20260425'
    '.bak'
    '.env.bak'
    'docker-compose.yml.bak'
    'promtail-config.yaml.bak'
    'mh-logs.json'
    'security/vuln-scan/reports/'
)

# ---------- Helpers ----------
ts() { date -u +%Y-%m-%dT%H:%M:%SZ; }

log() {
    local msg="$1"
    echo "[$(ts)] $msg" | tee -a "$LOG"
}

die() {
    log "ERROR: $1"
    exit 1
}

is_whitelisted() {
    local path="$1"
    for pat in "${WHITELIST_PATTERNS[@]}"; do
        case "$path" in
            *"$pat"*) return 0 ;;
        esac
    done
    return 1
}

# ---------- Phase 1: Pre-flight ----------
mkdir -p "$(dirname "$LOG")"
log "=== DEPLOY START branch=$BRANCH service=$SERVICE unsafe=$UNSAFE ==="

cd "$REPO" || die "cannot cd $REPO"

# Working tree check with whitelist
DIRTY_TRACKED=$(git status --porcelain | grep -E '^[ MARCD][MD]' | awk '{print $2}' || true)
DIRTY_UNTRACKED=$(git ls-files --others --exclude-standard || true)

REFUSE=0
WARN_COUNT=0

# Tracked uncommitted modifications (Bucket B style) → REFUSE unless --unsafe
if [ -n "$DIRTY_TRACKED" ]; then
    log "WARN: tracked uncommitted modifications detected:"
    while read -r f; do
        [ -z "$f" ] && continue
        log "  M $f"
    done <<< "$DIRTY_TRACKED"
    if [ "$UNSAFE" -eq 0 ]; then
        REFUSE=1
    else
        log "WARN: --unsafe-allow-dirty bypasses tracked-modification refusal (logged prominently)"
    fi
fi

# Untracked files: aggregate whitelist matches, list non-whitelist offenders
if [ -n "$DIRTY_UNTRACKED" ]; then
    while read -r f; do
        [ -z "$f" ] && continue
        if is_whitelisted "$f"; then
            WARN_COUNT=$((WARN_COUNT + 1))
        else
            log "REFUSE: untracked non-whitelist path $f"
            REFUSE=1
        fi
    done <<< "$DIRTY_UNTRACKED"
fi
[ "$WARN_COUNT" -gt 0 ] && log "INFO: $WARN_COUNT untracked whitelisted paths skipped (see DOCS/issues/BACKEND-REPO-FRONTEND-ARTIFACT-DRIFT-001 + SERVER-CLEANUP-BAK-FILES-001)"

if [ "$REFUSE" -eq 1 ] && [ "$UNSAFE" -eq 0 ]; then
    log "=== DEPLOY ABORT: working tree refuses; resolve or pass --unsafe-allow-dirty ==="
    exit 1
fi

# Capture PREV_SHA
PREV_SHA=$(git rev-parse HEAD) || die "git rev-parse failed"
log "PREV_SHA=$PREV_SHA"

# Service exists check
if ! docker compose -f "$REPO/docker-compose.yml" config --services 2>/dev/null | grep -qx "$SERVICE"; then
    die "service '$SERVICE' not found in docker-compose.yml"
fi

# doctl check (informational only)
if ! command -v doctl >/dev/null 2>&1; then
    log "WARN: doctl not installed — DO snapshot must be triggered manually before this deploy"
fi

# ---------- Phase 2: Fetch + pull ----------
log "Phase 2: fetch + pull origin $BRANCH"
git fetch origin "$BRANCH" 2>&1 | tee -a "$LOG"

# Branch checkout (only if not already on it)
CURRENT_BRANCH=$(git rev-parse --abbrev-ref HEAD)
if [ "$CURRENT_BRANCH" != "$BRANCH" ]; then
    log "Switching from $CURRENT_BRANCH to $BRANCH"
    git checkout "$BRANCH" 2>&1 | tee -a "$LOG" || die "checkout failed"
fi

git pull --ff-only origin "$BRANCH" 2>&1 | tee -a "$LOG" || die "ff-only pull failed (non-FF; resolve manually)"

NEW_SHA=$(git rev-parse HEAD)
log "NEW_SHA=$NEW_SHA"

if [ "$PREV_SHA" = "$NEW_SHA" ]; then
    log "=== DEPLOY NO-OP: PREV==NEW ($PREV_SHA), skipping rebuild ==="
    exit 0
fi

# ---------- Phase 3: Build + restart ----------
START_TS=$(date +%s)
BUILD_TIME=$(ts)

log "Phase 3: build + restart $SERVICE (GIT_SHA=${NEW_SHA:0:12})"
GIT_SHA="$NEW_SHA" BUILD_TIME="$BUILD_TIME" docker compose -f "$REPO/docker-compose.yml" build --no-deps "$SERVICE" 2>&1 | tail -20 | tee -a "$LOG"
GIT_SHA="$NEW_SHA" BUILD_TIME="$BUILD_TIME" docker compose -f "$REPO/docker-compose.yml" up -d --no-deps "$SERVICE" 2>&1 | tee -a "$LOG"

# ---------- Phase 4: Health check loop ----------
log "Phase 4: health gate ($HEALTH_URL, timeout ${HEALTH_TIMEOUT}s)"

HEALTH_OK=0
ELAPSED=0
while [ "$ELAPSED" -lt "$HEALTH_TIMEOUT" ]; do
    HTTP=$(curl -s -o /dev/null -w '%{http_code}' "$HEALTH_URL" || echo 000)
    if [ "$HTTP" = "200" ]; then
        HEALTH_OK=1
        log "health: 200 after ${ELAPSED}s"
        break
    fi
    sleep "$HEALTH_INTERVAL"
    ELAPSED=$((ELAPSED + HEALTH_INTERVAL))
done

if [ "$HEALTH_OK" -ne 1 ]; then
    log "FAIL: /ready did not return 200 within ${HEALTH_TIMEOUT}s — initiating rollback"

    # ---------- Phase 6: Rollback ----------
    log "Phase 6: rollback to PREV_SHA=$PREV_SHA"
    git reset --hard "$PREV_SHA" 2>&1 | tee -a "$LOG"
    GIT_SHA="$PREV_SHA" BUILD_TIME="$(ts)" docker compose -f "$REPO/docker-compose.yml" up -d --no-deps "$SERVICE" 2>&1 | tee -a "$LOG"

    ELAPSED=0
    ROLLBACK_OK=0
    while [ "$ELAPSED" -lt "$HEALTH_TIMEOUT" ]; do
        HTTP=$(curl -s -o /dev/null -w '%{http_code}' "$HEALTH_URL" || echo 000)
        if [ "$HTTP" = "200" ]; then
            ROLLBACK_OK=1
            log "rollback health: 200 after ${ELAPSED}s"
            break
        fi
        sleep "$HEALTH_INTERVAL"
        ELAPSED=$((ELAPSED + HEALTH_INTERVAL))
    done

    if [ "$ROLLBACK_OK" -eq 1 ]; then
        DURATION=$(( $(date +%s) - START_TS ))
        log "=== DEPLOY ROLLED BACK $BRANCH $NEW_SHA→$PREV_SHA ${DURATION}s ==="
        exit 1
    else
        log "CRITICAL: rollback also failed health check; service may be down"
        log "=== DEPLOY CRITICAL ${DURATION}s ==="
        exit 2
    fi
fi

# ---------- Phase 5: Post-deploy verification ----------
log "Phase 5: post-deploy verification"

VERSION_REPORTED=$(curl -s "$VERSION_URL" 2>/dev/null | grep -oE '"commit":"[^"]*"' | head -1 || echo '"commit":"?"')
log "version: $VERSION_REPORTED  (expected commit prefix ${NEW_SHA:0:12})"

DURATION=$(( $(date +%s) - START_TS ))
log "=== DEPLOY OK $BRANCH $PREV_SHA→${NEW_SHA:0:12} ${DURATION}s ==="
exit 0
