#!/bin/bash
# Schema-contract CI RATCHET.
# Fails ONLY when a NEW ghost-column signature appears vs the accepted baseline.
# The existing set is grandfathered (a gate that fails on 207 pre-existing violations
# gets disabled within a week). Baseline SHRINKS as drift is cleaned; it must never grow
# silently. Does NOT fail on count>0.
#
# Prereq: /root/cols.txt (authoritative column map) refreshed from the target DB, e.g.:
#   psql ... -tAF"|" -c "SELECT table_name,column_name FROM information_schema.columns
#                        WHERE table_schema='public'" > /root/cols.txt
set -uo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
BASELINE="${BASELINE:-$DIR/baseline_signatures.txt}"
CUR="$(mktemp)"
python3 "$DIR/schema_scan.py" --signatures > "$CUR"

NEW="$(comm -13 <(sort -u "$BASELINE") <(sort -u "$CUR"))"
if [ -n "$NEW" ]; then
  echo "SCHEMA-CONTRACT RATCHET FAILED — new ghost-column reference(s) introduced:"
  echo "$NEW" | sed 's/^/  + /'
  echo "Fix the column reference; if intentional, justify and update baseline_signatures.txt."
  rm -f "$CUR"; exit 1
fi
GONE="$(comm -23 <(sort -u "$BASELINE") <(sort -u "$CUR"))"
if [ -n "$GONE" ]; then
  echo "INFO: cleaned up (safe to remove from baseline):"
  echo "$GONE" | sed 's/^/  - /'
fi
echo "OK: no new ghost-column references ($(sort -u "$CUR" | wc -l | tr -d ' ') current, $(sort -u "$BASELINE" | wc -l | tr -d ' ') baseline)."
rm -f "$CUR"
