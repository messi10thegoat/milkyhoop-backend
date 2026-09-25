#!/bin/bash
# mh-rotasi-postgres.sh -- rotasi sandi peran `postgres` (#41). Hanya di jendela yang dibuka MASTER, sesudah
# rilis "sandi dari env" (#41 kode) LIVE. TIDAK PERNAH mencetak sandi: nilai pindah lewat berkas 600 / stdin.
#
# Kenapa aman dipulihkan: pg_hba kontainer = socket lokal `trust`, jadi ALTER ROLE lewat `docker exec psql`
# (socket) selalu jalan apa pun sandinya; TCP dari jaringan = scram-sha-256 -> hanya klien jaringan
# (api_gateway, auth_service) yang bergantung pada sandi.
#
# Urutan: 0 prasyarat (kode live, image == berjalan, TCP sandi lama OK) -> backup pg_dump -> STOP layanan
# (gagal-tertutup 502) -> ALTER ROLE (stdin) -> .env -> uji TCP DUA SISI (baru OK, lama DITOLAK) -> up -d
# layanan -> verifikasi -> gagal di mana pun = ROLLBACK (ALTER ke sandi lama + .env lama + up -d).
#
# Override untuk uji kering scratch (bawaan = produksi):
ROT_DIR=${ROT_DIR:-/root/milkyhoop-dev}
PG=${ROT_PG_CTR:-milkyhoop-dev-postgres-1}
NET=${ROT_NET:-milkyhoop_dev_network}
PGHOST_NET=${ROT_PG_HOST:-postgres}
PGIMG=${ROT_PG_IMAGE:-pgvector/pgvector:pg14}
DBN=${ROT_DB:-milkydb}
SERVICES=${ROT_SERVICES:-"api_gateway auth_service"}
GW=${ROT_GW_CTR:-milkyhoop-dev-api_gateway}
HEALTH=${ROT_HEALTH:-http://localhost:8001/healthz}
INFO=${ROT_INFO_URL:-http://localhost:8001/api/tenant/grapgrap-manado/info}
BK=${ROT_BACKUPS:-/root/backups}
SKIP_SVC=${ROT_SKIP_SERVICES:-}
set -uo pipefail
cd "$ROT_DIR" || exit 3
umask 077
TS=$(date -u +%Y%m%dT%H%M%S)
OLDF=$BK/pg-pw-old-$TS; NEWF=$BK/pg-pw-new-$TS; ENVB=$BK/env-pre-pg-rotation-$TS
log() { echo "[$(date -u +%H:%M:%S)] $*"; }
tolak() { shred -u $OLDF $ENVB 2>/dev/null; rm -f $OLDF $ENVB; log "REFUSE: $*"; exit 3; }  # langkah 0: tanpa jejak sandi

tcp_ok() {  # tcp_ok <pwfile> -> cetak "1" bila login TCP berhasil. Sandi lewat --env-file 600, bukan argv.
  local ef; ef=$(mktemp); chmod 600 "$ef"
  { printf 'PGPASSWORD='; cat "$1"; printf '\n'; } > "$ef"
  docker run --rm --network "$NET" --env-file "$ef" "$PGIMG" \
    psql -h "$PGHOST_NET" -U postgres -d "$DBN" -Atqc "select 1" 2>/dev/null
  shred -u "$ef" 2>/dev/null || rm -f "$ef"
}
alter_pw() {  # alter_pw <pwfile> -- SQL lewat stdin ke psql socket (trust); log_statement dimatikan di sesi.
  { printf "SET log_statement = 'none';\nALTER ROLE postgres PASSWORD '"; cat "$1"; printf "';\n"; } \
    | docker exec -i "$PG" psql -U postgres -d "$DBN" -v ON_ERROR_STOP=1 -q >/dev/null
}
set_env_pw() {  # set_env_pw <pwfile> -- ganti baris DB_PASSWORD di .env (path lewat env, nilai tak lewat argv)
  ROT_PWF="$1" python3 - <<'PY'
import os
pw = open(os.environ["ROT_PWF"]).read().strip()
s = open(".env").read().split("\n")
n = 0
for i, l in enumerate(s):
    if l.startswith("DB_PASSWORD="):
        s[i] = "DB_PASSWORD=" + pw; n += 1
assert n == 1, n
open(".env", "w").write("\n".join(s))
PY
}
svc_stop()  { [ -n "$SKIP_SVC" ] && return 0; docker compose stop $SERVICES >/dev/null 2>&1; }
svc_start() {
  [ -n "$SKIP_SVC" ] && return 0
  docker logs "$GW" > /root/logs/$(basename "$GW")-$(date +%s).log 2>&1 || true
  docker compose up -d --no-deps $SERVICES >/dev/null 2>&1 || { log "   compose up GAGAL"; return 1; }
  for i in $(seq 1 90); do [ "$(curl -s -o /dev/null -w '%{http_code}' $HEALTH)" = 200 ] && { log "   gateway healthz 200 sesudah ${i}s"; return 0; }; sleep 1; done
  log "   gateway TIDAK sehat sesudah 90s"; return 1
}

log "== 0 prasyarat"
grep -E '^DB_PASSWORD=' .env | head -1 | cut -d= -f2- | tr -d '\n' > $OLDF
[ -s $OLDF ] || { rm -f $OLDF; log "REFUSE: DB_PASSWORD kosong di .env"; exit 3; }
cp .env $ENVB; chmod 600 $ENVB $OLDF
if [ -z "$SKIP_SVC" ]; then
  docker exec $GW grep -q "_DB = settings.get_db_config()" /app/backend/api_gateway/app/services/realtime.py \
    || tolak "rilis #41 (sandi dari env) belum LIVE"
  lit=$(docker exec -i $GW sh -c 'read -r P; grep -rlF --include=*.py -- "$P" /app/backend/api_gateway/app | wc -l' < $OLDF)
  [ "$lit" = 0 ] || tolak "$lit berkas app/ yang BERJALAN masih memuat sandi literal"
  for s in $SERVICES; do  # syarat MASTER: image yang akan dipakai up -d == image kontainer berjalan
    # layanan ber-`build:` tanpa `image:` -> `up -d --no-deps` (tanpa --build) memakai tag milik kontainer
    # (.Config.Image, mis. milkyhoop-dev-api_gateway). Tag itu harus menunjuk image yang SEDANG berjalan.
    c=$(docker compose ps -q $s); [ -n "$c" ] || tolak "$s tak berjalan"
    run=$(docker inspect -f '{{.Image}}' "$c"); tag=$(docker inspect -f '{{.Config.Image}}' "$c")
    want=$(docker image inspect -f '{{.Id}}' "$tag" 2>/dev/null)
    [ -n "$want" ] && [ "$run" = "$want" ] || tolak "$s image berjalan ${run:7:12} != tag $tag ${want:7:12} (recreate akan mengganti versi)"
    log "   $s image == tag compose ($tag ${run:7:12})"
  done
fi
[ "$(tcp_ok $OLDF)" = 1 ] || tolak "alat uji TCP gagal dengan sandi SEKARANG (alat rusak, bukan sandi)"
log "   uji TCP sandi sekarang: OK (alat terbukti hijau)"
if [ -n "${ROT_CEK_SAJA:-}" ]; then  # prasyarat saja (baca-saja): buang salinan sandi, tak menyentuh apa pun
  shred -u $OLDF $ENVB 2>/dev/null || rm -f $OLDF $ENVB
  log "CEK-SAJA: prasyarat LULUS, tak ada yang diubah"; exit 0
fi

log "== 1 backup pg_dump"
docker exec "$PG" pg_dump -U postgres -Fc "$DBN" > $BK/$DBN-pre-pgrot-$TS.dump && [ -s $BK/$DBN-pre-pgrot-$TS.dump ] \
  || { log "REFUSE: backup gagal -- belum ada yang disentuh"; exit 4; }
log "   dump $(du -h $BK/$DBN-pre-pgrot-$TS.dump | cut -f1)"

log "== 2 stop layanan (gagal-tertutup)"
svc_stop || { log "REFUSE: stop gagal"; exit 4; }

log "== 3 sandi baru -> ALTER ROLE -> .env"
openssl rand -base64 64 | tr -dc 'A-Za-z0-9' | head -c 32 > $NEWF; chmod 600 $NEWF
[ "$(wc -c < $NEWF)" = 32 ] || { log "sandi baru bukan 32 karakter -> berhenti"; svc_start; exit 5; }
ROLLBACK=0
alter_pw $NEWF || { log "   ALTER ROLE GAGAL"; ROLLBACK=1; }
[ $ROLLBACK = 0 ] && { set_env_pw $NEWF || { log "   .env GAGAL"; ROLLBACK=1; }; }

if [ $ROLLBACK = 0 ]; then
  log "== 4 uji TCP dua sisi"
  NB=$(tcp_ok $NEWF); LM=$(tcp_ok $OLDF)
  log "   sandi baru: ${NB:-DITOLAK} | sandi lama: ${LM:-DITOLAK}"
  [ "$NB" = 1 ] && [ -z "$LM" ] || ROLLBACK=1
fi
T0=$(date -u +%Y-%m-%dT%H:%M:%SZ)
if [ $ROLLBACK = 0 ]; then
  log "== 5 start layanan"
  svc_start || ROLLBACK=1
fi
if [ $ROLLBACK = 0 ] && [ -z "$SKIP_SVC" ]; then
  log "== 6 verifikasi"
  sleep 5
  PE=$(docker logs --since "$T0" $GW 2>&1 | grep -c "PolicyEngine initialized")
  PEF=$(docker logs --since "$T0" $GW 2>&1 | grep -ciE "Failed to init PolicyEngine|password authentication failed")
  PGF=$(docker logs --since "$T0" "$PG" 2>&1 | grep -c "password authentication failed")
  HZ=$(curl -s -o /dev/null -w '%{http_code}' $HEALTH); IF=$(curl -s -o /dev/null -w '%{http_code}' $INFO)
  AU=$(docker inspect -f '{{.State.Status}}' $(docker compose ps -q auth_service) 2>/dev/null)
  log "   PolicyEngine initialized=$PE | gagal=$PEF | pg auth gagal=$PGF | healthz=$HZ | info tenant=$IF | auth_service=$AU"
  [ "$PE" -ge 1 ] && [ "$PEF" = 0 ] && [ "$PGF" = 0 ] && [ "$HZ" = 200 ] && [ "$IF" = 200 ] && [ "$AU" = running ] || ROLLBACK=1
fi
[ -n "${ROT_FORCE_FAIL:-}" ] && [ $ROLLBACK = 0 ] && { log "   ROT_FORCE_FAIL -> verifikasi dianggap GAGAL (uji jalur rollback)"; ROLLBACK=1; }

if [ $ROLLBACK = 1 ]; then
  log "== ROLLBACK: stop layanan -> ALTER ke sandi lama (socket) -> .env lama -> start"
  svc_stop
  alter_pw $OLDF && log "   ALTER ke sandi lama OK" || log "   !!! ALTER ke sandi lama GAGAL -- ulangi: alter_pw $OLDF"
  cp $ENVB .env
  svc_start || log "   !!! layanan belum sehat -- cd $ROT_DIR && docker compose up -d --no-deps $SERVICES"
  log "   sesudah rollback: TCP sandi lama=$(tcp_ok $OLDF) | sandi baru=$(tcp_ok $NEWF || true)"
  shred -u $NEWF 2>/dev/null || rm -f $NEWF
  exit 1
fi
shred -u $NEWF 2>/dev/null || rm -f $NEWF
log "ROTASI SELESAI $(date -u +%FT%TZ). Sandi lama tersimpan root-only di $OLDF dan $ENVB (shred = GO pemilik)."
