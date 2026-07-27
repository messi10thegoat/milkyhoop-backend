#!/usr/bin/env bash
# =============================================================================
# verdict.sh — shared rc-based assertion helpers for dp_flow step scripts.
# Sourced AFTER state.env. Each failed assertion increments VERDICT_FAILS and prints a
# "FAIL — ..." line; finish() exits 1 when any failed. This makes the child's EXIT CODE the
# PRIMARY failure signal (run_all.sh gates on rc). The printed FAIL token is only a safety belt.
# Rationale (owner directive): string/token matching alone silently misses a real failure whose
# format drifts — the exact silent-fallback class we chased all session. rc != 0 cannot drift.
# =============================================================================
VERDICT_FAILS=0
_pass(){ echo "  PASS — $1"; }
_fail(){ echo "  FAIL — $1"; VERDICT_FAILS=$((VERDICT_FAILS+1)); }
aeq(){   [ "$2" = "$3" ]  && _pass "$1 (=$2)"                    || _fail "$1: got '$2' want '$3'"; }
ane(){   [ "$2" != "$3" ] && _pass "$1 (=$2, differs from $3)"  || _fail "$1: '$2' must differ from '$3'"; }
atrue(){ case "$2" in t|true|TRUE|True|1|yes) _pass "$1 (=$2)";; *) _fail "$1: got '$2' want truthy";; esac; }
acontains(){ case "$2" in *"$3"*) _pass "$1 (contains '$3')";; *) _fail "$1: '$3' NOT found in output";; esac; }
finish(){
  if [ "${VERDICT_FAILS:-0}" -ne 0 ]; then
    echo "STEP RESULT: FAILED — $VERDICT_FAILS assertion(s) failed"; exit 1
  fi
  echo "STEP RESULT: OK — all assertions passed"
}
