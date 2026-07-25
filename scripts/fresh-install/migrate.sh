#!/bin/bash
# ---------------------------------------------------------------------------
# migrate.sh — Milkyhoop migration tracking + incremental runner.
#
# Closes the "no migration provenance" regression vector: milkydb had NO
# tracking table of any kind (no Flyway, no _prisma_migrations). The
# fresh-install runner (run_migrations_v9.sh) is a REPLAY-on-empty tool and is
# NOT idempotent against a populated DB. This adds minimal, honest tracking.
#
# Modes:
#   backfill : record the CURRENT set of V*.sql as already-applied/skipped
#              (applied_by='BACKFILL'). One-time, for a DB built by the recipe
#              before tracking existed. Never runs migration SQL. ON CONFLICT
#              DO NOTHING so it never clobbers real 'runner' records.
#   apply    : apply PENDING migrations (not yet in schema_migrations), in
#              V-sorted order, each wrapped with its tracking-INSERT in ONE
#              --single-transaction (atomic; crash-between cannot double-apply).
#              Checksum FAIL-LOUD: a recorded version whose on-disk file changed
#              aborts the run (catches file-edited-after-apply).
#   verify   : compare on-disk V*.sql vs schema_migrations; report drift
#              (untracked file, checksum mismatch, orphan record). Exit != 0 on
#              any drift. Catches manual psql / out-of-band edits.
#
# Env: PGDB (default milkydb), MIGDIR, PGUSER, CONTAINER, SKIPLIST.
# ---------------------------------------------------------------------------
set -uo pipefail

MODE="${1:-}"
PGDB="${PGDB:-milkydb}"
PGUSER="${PGUSER:-postgres}"
CONTAINER="${CONTAINER:-milkyhoop-dev-postgres-1}"
MIGDIR="${MIGDIR:-/root/milkyhoop-dev/backend/migrations}"
SELF_DIR="$(cd "$(dirname "$0")" && pwd)"
SKIPLIST="${SKIPLIST:-$SELF_DIR/migration_skip_list.sh}"

declare -A SKIP_REASON
if [ -f "$SKIPLIST" ]; then
    # shellcheck disable=SC1090
    source "$SKIPLIST"
else
    echo "WARN: skip list not found at $SKIPLIST — no historical skips known." >&2
fi

# psql helpers (all pinned to $PGDB inside the container)
PG()  { docker exec -i "$CONTAINER" psql -U "$PGUSER" -d "$PGDB" -v ON_ERROR_STOP=1 "$@"; }
PGT() { docker exec -i "$CONTAINER" psql -U "$PGUSER" -d "$PGDB" -tAq "$@"; }

checksum()   { sha256sum "$1" | awk '{print $1}'; }
sql_escape() { printf "%s" "$1" | sed "s/'/''/g"; }

ensure_table() {
    PG >/dev/null <<'SQL'
CREATE TABLE IF NOT EXISTS schema_migrations (
    version     TEXT PRIMARY KEY,
    checksum    TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'applied' CHECK (status IN ('applied','skipped','untracked-external')),
    applied_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    applied_by  TEXT NOT NULL DEFAULT 'runner'
);
SQL
}

# Sentinel rows: honesty markers for schema created OUTSIDE any VNNN (Step-0 stub in
# run_migrations_v9.sh + gap_patch.sh). Idempotent; checksum = sha256 of the creating
# script so edits to those scripts are detectable by verify(). Called from backfill() so
# every bootstrapped DB gets them automatically — they no longer depend on a manual insert.
insert_sentinels() {
    local ver scr cs
    for pair in "STEP0_STUB:run_migrations_v9.sh" "GAP_PATCH:gap_patch.sh"; do
        ver="${pair%%:*}"; scr="$SELF_DIR/${pair##*:}"
        [ -f "$scr" ] || { echo "WARN: sentinel source missing, skipped: $scr" >&2; continue; }
        cs="$(checksum "$scr")"
        PGT >/dev/null -c "INSERT INTO schema_migrations(version,checksum,status,applied_by)
                           VALUES('$ver','$cs','untracked-external','sentinel')
                           ON CONFLICT (version) DO NOTHING;"
    done
}

backfill() {
    ensure_table
    local n_app=0 n_skip=0 base cs st
    for f in $(ls "$MIGDIR"/V*.sql 2>/dev/null | sort -V); do
        base="$(basename "$f")"
        cs="$(checksum "$f")"
        st="applied"
        [ -n "${SKIP_REASON[$base]:-}" ] && st="skipped"
        # Requirement 3 (honest): applied_by='BACKFILL'. ON CONFLICT DO NOTHING so
        # a real 'runner' record is never overwritten by a later backfill.
        PGT >/dev/null -c "INSERT INTO schema_migrations(version,checksum,status,applied_at,applied_by)
                           VALUES('$(sql_escape "$base")','$cs','$st',NOW(),'BACKFILL')
                           ON CONFLICT (version) DO NOTHING;"
        if [ "$st" = "applied" ]; then n_app=$((n_app+1)); else n_skip=$((n_skip+1)); fi
    done
    insert_sentinels
    echo "BACKFILL done on $PGDB: applied=$n_app skipped=$n_skip total=$((n_app+n_skip)) (+2 sentinels)"
}

apply() {
    ensure_table
    local n_new=0 n_have=0 n_skip=0 base cs row rstatus rcs rc
    for f in $(ls "$MIGDIR"/V*.sql 2>/dev/null | sort -V); do
        base="$(basename "$f")"
        cs="$(checksum "$f")"
        row="$(PGT -c "SELECT status||'|'||checksum FROM schema_migrations WHERE version='$(sql_escape "$base")';")"
        if [ -n "$row" ]; then
            rstatus="${row%%|*}"; rcs="${row##*|}"
            # Requirement 2: checksum FAIL-LOUD.
            if [ "$rcs" != "$cs" ]; then
                echo "FATAL: checksum drift for $base" >&2
                echo "       recorded=$rcs" >&2
                echo "       on-disk =$cs" >&2
                echo "       A file that was already applied has changed. Refusing to continue." >&2
                exit 3
            fi
            n_have=$((n_have+1)); continue
        fi
        # Not recorded yet.
        if [ -n "${SKIP_REASON[$base]:-}" ]; then
            PGT >/dev/null -c "INSERT INTO schema_migrations(version,checksum,status,applied_by)
                               VALUES('$(sql_escape "$base")','$cs','skipped','runner');"
            echo "SKIP  | $base | ${SKIP_REASON[$base]}"
            n_skip=$((n_skip+1)); continue
        fi
        # Requirement 1: migration SQL + tracking INSERT in ONE --single-transaction.
        echo "APPLY | $base"
        { cat "$f"
          printf "\nINSERT INTO schema_migrations(version,checksum,status,applied_by) VALUES('%s','%s','applied','runner');\n" \
                 "$(sql_escape "$base")" "$cs"
        } | docker exec -i "$CONTAINER" psql -U "$PGUSER" -d "$PGDB" -v ON_ERROR_STOP=1 --single-transaction
        rc=$?
        if [ "$rc" -ne 0 ]; then
            echo "FATAL: apply failed for $base (psql rc=$rc). Transaction rolled back; nothing recorded. STOPPING." >&2
            exit 4
        fi
        n_new=$((n_new+1))
    done
    echo "APPLY done on $PGDB: newly-applied=$n_new already-present=$n_have newly-skipped=$n_skip"
}

verify() {
    ensure_table
    local drift=0 base cs row v
    # On-disk vs table.
    for f in $(ls "$MIGDIR"/V*.sql 2>/dev/null | sort -V); do
        base="$(basename "$f")"
        cs="$(checksum "$f")"
        row="$(PGT -c "SELECT checksum FROM schema_migrations WHERE version='$(sql_escape "$base")';")"
        if [ -z "$row" ]; then
            echo "DRIFT: on-disk but NOT tracked        : $base"; drift=$((drift+1))
        elif [ "$row" != "$cs" ]; then
            echo "DRIFT: checksum mismatch (edited?)     : $base recorded=$row disk=$cs"; drift=$((drift+1))
        fi
    done
    # Table vs on-disk (orphans).
    while read -r v; do
        [ -z "$v" ] && continue
        [ -f "$MIGDIR/$v" ] || { echo "DRIFT: tracked but file MISSING        : $v"; drift=$((drift+1)); }
    done < <(PGT -c "SELECT version FROM schema_migrations WHERE status <> 'untracked-external' ORDER BY version;")
    # Sentinel checksums: external-script files are outside VNNN, so a mismatch is a WARNING
    # (informational), never a drift failure. Makes the recorded checksum non-decorative.
    for pair in "STEP0_STUB:run_migrations_v9.sh" "GAP_PATCH:gap_patch.sh"; do
        v="${pair%%:*}"; base="$SELF_DIR/${pair##*:}"
        row="$(PGT -c "SELECT checksum FROM schema_migrations WHERE version='$v';")"
        [ -z "$row" ] && continue
        [ -f "$base" ] || { echo "WARN: sentinel $v recorded but source missing: $base"; continue; }
        cs="$(checksum "$base")"
        [ "$row" != "$cs" ] && echo "WARN: sentinel $v checksum changed (external script edited): recorded=$row disk=$cs"
    done
    if [ "$drift" -eq 0 ]; then
        echo "VERIFY OK on $PGDB: no drift ($(PGT -c "SELECT count(*) FROM schema_migrations;") tracked)"
    else
        echo "VERIFY FAILED on $PGDB: $drift drift(s) found"; exit 5
    fi
}

case "$MODE" in
    backfill) backfill ;;
    apply)    apply ;;
    verify)   verify ;;
    *) echo "usage: PGDB=<db> $0 {backfill|apply|verify}"; exit 1 ;;
esac
