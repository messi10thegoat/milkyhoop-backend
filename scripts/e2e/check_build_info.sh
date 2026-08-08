#!/bin/bash
# GATE provenance FE: BUILD_INFO.json yang DISAJIKAN harus cocok dengan sha yang di-pin.
# Dipakai sebagai gate deploy: exit 0 = lulus, exit 1 = TOLAK.
#   usage: check_build_info.sh <base_url> <expected_sha>
set -uo pipefail
BASE=${1:?base_url}
EXPECT=${2:?expected_sha}

BODY=$(curl -fsS "$BASE/BUILD_INFO.json" 2>/dev/null) || {
  echo "GATE MERAH: BUILD_INFO.json tak terambil dari $BASE"; exit 1; }

GOT=$(printf '%s' "$BODY" | python3 -c "import sys,json;print(json.load(sys.stdin).get('source_sha',''))" 2>/dev/null)
BUNDLE=$(printf '%s' "$BODY" | python3 -c "import sys,json;print(json.load(sys.stdin).get('main_bundle',''))" 2>/dev/null)
CLEAN=$(printf '%s' "$BODY" | python3 -c "import sys,json;print(json.load(sys.stdin).get('tree_clean',''))" 2>/dev/null)

[ -n "$GOT" ] || { echo "GATE MERAH: source_sha kosong/absen"; exit 1; }

if [ "$GOT" != "$EXPECT" ]; then
  echo "GATE MERAH: sha TIDAK COCOK"
  echo "  diharap : $EXPECT"
  echo "  disajikan: $GOT"
  exit 1
fi

if [ "$CLEAN" != "True" ] && [ "$CLEAN" != "true" ]; then
  echo "GATE MERAH: tree_clean=$CLEAN — build dari working tree kotor"; exit 1
fi

echo "GATE HIJAU: sha $GOT · bundle $BUNDLE · tree_clean=$CLEAN"
exit 0
