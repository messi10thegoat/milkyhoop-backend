#!/bin/bash
# window_item.sh <item#> <worktree> <branch> <marker-file-rel-to-app> <marker-text>
# One window item: rebase onto CURRENT deploy/master -> force-push branch (lease) -> mh-gerbang-merge LULUS
# -> ff-only main tree -> push master -> verify remote -> mh-restart -> StartedAt moved -> marker in the
# RUNNING container's file -> healthz 200. Refuses (non-zero) at the first failed step; nothing after it runs.
# MODE=reload (OPT-IN, 26 Sep 2026): ganti mh-restart dengan scripts/ops/mh-reload.sh (SIGHUP; port tetap terbuka ->
# nol 502 pengguna saat startup 10-25 dtk). Bukti mode reload = PID worker berganti + "Received SIGHUP" + healthz -m 5
# + penanda (StartedAt TIDAK bergeser pada HUP, jadi TIDAK dipakai sebagai bukti). Default tetap MODE=restart.
# Hanya untuk perubahan KODE; .env/image -> restart/recreate.
set -uo pipefail
N=$1; W=$2; B=$3; MF=$4; MT=$5; MAIN=/root/milkyhoop-dev; G=milkyhoop-dev-api_gateway
log(){ echo "[item $N] $*"; }
cd "$W" || { log "REFUSE: no worktree"; exit 3; }
[ "$(git rev-parse --abbrev-ref HEAD)" = "$B" ] || { log "REFUSE: not on $B"; exit 3; }
[ -z "$(git status --porcelain)" ] || { log "REFUSE: worktree dirty"; exit 3; }
git -C $MAIN fetch -q deploy || { log "REFUSE: fetch"; exit 3; }
LIVE=$(git -C $MAIN rev-parse HEAD)
[ "$LIVE" = "$(git -C $MAIN rev-parse deploy/master)" ] || { log "REFUSE: main tree HEAD != deploy/master"; exit 3; }
OLD=$(git rev-parse HEAD)
if ! git -c user.email=backend@milkyhoop -c user.name=BACKEND rebase -q "$LIVE" >/tmp/rebase_$N.log 2>&1; then
  git rebase --abort 2>/dev/null; log "REFUSE: rebase conflict"; cat /tmp/rebase_$N.log | tail -5; exit 4
fi
NEW=$(git rev-parse HEAD)
git merge-base --is-ancestor "$LIVE" "$NEW" || { log "REFUSE: rebased head not a descendant of live"; exit 4; }
git push -q --force-with-lease="$B:$OLD" deploy "$B" 2>/tmp/push_$N.log || { log "REFUSE: push branch"; cat /tmp/push_$N.log; exit 5; }
bash $MAIN/scripts/ops/mh-gerbang-merge.sh "$B" > /tmp/gate_$N.log 2>&1; grc=$?
tail -1 /tmp/gate_$N.log
[ $grc -eq 0 ] && grep -q "^LULUS" /tmp/gate_$N.log || { log "REFUSE: merge gate rc=$grc"; exit 6; }
git -C $MAIN merge --ff-only "$B" >/tmp/ff_$N.log 2>&1 || { log "REFUSE: ff"; cat /tmp/ff_$N.log; exit 7; }
[ "$(git -C $MAIN rev-parse HEAD)" = "$NEW" ] || { log "ABORT: main HEAD != $NEW"; exit 7; }
git -C $MAIN push -q deploy master 2>/tmp/pushm_$N.log || { log "ABORT: push master"; cat /tmp/pushm_$N.log; exit 7; }
git -C $MAIN fetch -q deploy; [ "$(git -C $MAIN rev-parse deploy/master)" = "$NEW" ] || { log "ABORT: remote master != $NEW"; exit 7; }
MODE=${MODE:-restart}
before=$(docker inspect -f "{{.State.StartedAt}}" $G)
if [ "$MODE" = "reload" ]; then
  bash $MAIN/scripts/ops/mh-reload.sh api_gateway --penanda "$MF" "$MT" > /tmp/restart_$N.log 2>&1; rrc=$?
  after=$(docker inspect -f "{{.State.StartedAt}}" $G)
  [ $rrc -eq 0 ] || { log "LIVE-RED: mh-reload rc=$rrc (fallback: MODE=restart / scripts/ops/mh-restart.sh api_gateway)"; tail -12 /tmp/restart_$N.log; exit 8; }
  grep -q "^RELOAD|" /tmp/restart_$N.log || { log "LIVE-RED: mh-reload tanpa baris RELOAD (keadaan rusak/PULIH?)"; tail -8 /tmp/restart_$N.log; exit 8; }
elif [ "$MODE" = "restart" ]; then
  bash $MAIN/scripts/ops/mh-restart.sh api_gateway > /tmp/restart_$N.log 2>&1; rrc=$?
  after=$(docker inspect -f "{{.State.StartedAt}}" $G)
  [ $rrc -eq 0 ] || { log "LIVE-RED: mh-restart rc=$rrc"; tail -5 /tmp/restart_$N.log; exit 8; }
  [ "$before" != "$after" ] || { log "LIVE-RED: StartedAt did not move"; exit 8; }
else
  log "REFUSE: MODE=$MODE (restart|reload)"; exit 8
fi
m=$(docker exec $G grep -cF -- "$MT" "/app/backend/api_gateway/app/$MF" 2>/dev/null || true); m=$(echo "$m" | head -1)
[ "${m:-0}" -ge 1 ] || { log "LIVE-RED: marker not in running container file"; exit 9; }
for i in $(seq 1 30); do h=$(curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8001/healthz); [ "$h" = 200 ] && break; sleep 2; done
[ "$h" = 200 ] || { log "LIVE-RED: healthz $h"; exit 10; }
echo "ROW|$N|$(git -C $MAIN rev-parse --short HEAD)|$after|marker=$m healthz=$h mode=$MODE"
