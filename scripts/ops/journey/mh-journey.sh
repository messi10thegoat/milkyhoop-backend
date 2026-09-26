#!/usr/bin/env bash
# mh-journey.sh — uji PERJALANAN dokumen (SO, DP, proforma, ...) dengan handler NYATA pada SALINAN prod yang
# TERISOLASI. Dibangun 26 Sep 2026 (BACKEND-2, permintaan MASTER); pola tiga pagar:
#   1. data   : pg_dump prod SEGAR (baca-saja) -> kontainer postgres sendiri `pgjourney`; dump DIHAPUS (shred)
#               begitu dipulihkan; templat milkydb_journey_base -> tiap lengan mulai dari keadaan SAMA.
#   2. jaringan: `mh-journey-net` --internal; kontainer gateway sekali-pakai HANYA di sana -> router yang mencoba
#               prod/redis/minio/internet GAGAL, bukan menulis. Sandi DB prod TAK dioper (hanya kunci FLE, untuk
#               membaca kolom terenkripsi); env ditulis 600 dan di-shred sesudah tiap jalan.
#   3. pagar  : journey_pagar.periksa() sebelum tulisan pertama: nama DB != milkydb, system_identifier != prod
#               (DIUKUR tiap jalan), tabel penanda ada. uji_harness.py membuktikan 2 hijau + 4 MERAH + 0 bocor;
#               skenario TAK dijalankan bila baris terakhirnya bukan "HARNESS OK".
#
# Pemakaian (dari mana saja; skrip dibaca dari pohon utama kecuali MH_TREE diset ke worktree):
#   mh-journey.sh siapkan                     # dump segar + pulihkan + templat (ulang = ganti salinan)
#   mh-journey.sh jalankan <skenario> [A|B|AB] # default AB; skenario = scripts/ops/journey/skenario_<nama>.py
#   mh-journey.sh status
#   mh-journey.sh bersihkan                   # hapus kontainer + jaringan + /root/journey (data nyata HILANG)
# Keluar jalankan: 0 = semua lengan 0 FAIL (KNOWN boleh) · 1 = ada FAIL · 2 = harness/alat gagal (JANGAN dibaca lulus)
# Keluaran: /root/journey/out/<skenario>/<lengan>/NN_<langkah>.json (fixture e2e FE) + RINGKAS.json
# Lengan A = router nyata di app mini TANPA authz; B = app.main penuh + lifespan + middleware izin nyata
# (distub hanya: validate_token gRPC auth -> JWT HS256 rahasia acak per jalan, dan sesi Redis).
set -euo pipefail

TREE="${MH_TREE:-/root/milkyhoop-dev}"
H="$TREE/scripts/ops/journey"
J=/root/journey
NET=mh-journey-net
PG=pgjourney
PROD_PG=milkyhoop-dev-postgres-1
PROD_GW=milkyhoop-dev-api_gateway
umask 077

galat() { echo "mh-journey: $*" >&2; exit 2; }

siapkan() {
  mkdir -p "$J"
  docker network inspect "$NET" >/dev/null 2>&1 || docker network create --internal "$NET" >/dev/null
  [ "$(docker network inspect -f '{{.Internal}}' "$NET")" = "true" ] || galat "$NET bukan --internal"
  local img; img=$(docker inspect -f '{{.Config.Image}}' "$PROD_PG")
  docker rm -f "$PG" >/dev/null 2>&1 || true
  openssl rand -hex 16 > "$J/.pw"
  local tmp; tmp=$(mktemp -d "$J/dump.XXXX")
  trap 'shred -u "$tmp"/* 2>/dev/null; rmdir "$tmp" 2>/dev/null || true' RETURN
  docker exec "$PROD_PG" pg_dumpall -U postgres --roles-only --no-role-passwords > "$tmp/roles.sql"
  docker exec "$PROD_PG" pg_dump -U postgres -Fc milkydb > "$tmp/prod.dump"
  docker run -d --name "$PG" --network "$NET" -e POSTGRES_PASSWORD="$(cat "$J/.pw")" -v "$tmp":/j:ro "$img" >/dev/null
  local i; for i in $(seq 60); do
    docker exec "$PG" psql -U postgres -Atc "select 1" >/dev/null 2>&1 && break; sleep 1; done
  sleep 2
  docker exec "$PG" psql -U postgres -q -f /j/roles.sql >/dev/null 2>&1 || true
  docker exec "$PG" createdb -U postgres milkydb_journey
  docker exec "$PG" pg_restore -U postgres -d milkydb_journey --no-owner --no-acl /j/prod.dump \
    || galat "pg_restore gagal"
  docker exec "$PG" psql -U postgres -d milkydb_journey -qc "create table _journey_scratch(dibuat timestamptz default now()); insert into _journey_scratch default values"
  # DB bernama prod DI DALAM kontainer scratch = bahan kontrol merah uji_harness (pagar harus menolak nama itu)
  docker exec "$PG" createdb -U postgres milkydb
  docker exec "$PG" psql -U postgres -d milkydb -qc "create table _journey_scratch(i int)"
  docker exec "$PG" createdb -U postgres -T milkydb_journey milkydb_journey_base
  local a b
  a=$(docker exec "$PG" psql -U postgres -d milkydb_journey -Atc "select count(*) from journal_entries")
  b=$(docker exec "$PROD_PG" psql -U postgres -d milkydb -Atc "select count(*) from journal_entries")
  echo "siapkan: salinan prod dipulihkan (journal_entries scratch=$a prod=$b); dump di-shred"
}

reset_db() {
  docker exec "$PG" psql -U postgres -qc "select pg_terminate_backend(pid) from pg_stat_activity where datname in ('milkydb_journey','milkydb_journey_base') and pid<>pg_backend_pid()" >/dev/null
  docker exec "$PG" dropdb -U postgres milkydb_journey
  docker exec "$PG" createdb -U postgres -T milkydb_journey_base milkydb_journey
}

jalan_py() {   # jalan_py <skrip> [arg...] -- di kontainer gateway sekali-pakai, HANYA di $NET
  local env="$J/env.$$" sysid img
  sysid=$(docker exec "$PROD_PG" psql -U postgres -d milkydb -Atc "select system_identifier from pg_control_system()")
  [[ "$sysid" =~ ^[0-9]+$ ]] || galat "gagal mengukur system_identifier prod"
  { echo "DB_HOST=$PG"; echo "DB_PORT=5432"; echo "DB_USER=postgres"; echo "DB_NAME=milkydb_journey"
    echo "DB_PASSWORD=$(cat "$J/.pw")"
    echo "DATABASE_URL=postgresql://postgres:$(cat "$J/.pw")@$PG:5432/milkydb_journey"
    echo "JWT_SECRET=journey-$(openssl rand -hex 16)"; echo "INTERNAL_API_KEY=journey"
    echo "JOURNEY_PROD_SYSID=$sysid"
    grep -E "^FLE_(PRIMARY_KEK|BLIND_INDEX_SALT)=" "$TREE/.env" || true
    echo "REDIS_PASSWORD=x"; echo "MINIO_ENDPOINT=minio-tak-ada:9000"; echo "MINIO_ACCESS_KEY=x"; echo "MINIO_SECRET_KEY=x"
    echo "RESEND_API_KEY=re_journey_palsu"; echo "OPENAI_API_KEY=x"; echo "GOOGLE_API_KEY=x"
    echo "PYTHONPATH=/app:/app/backend/api_gateway/app:/app/backend/api_gateway/libs:/app/backend/services:/wt/backend/api_gateway"
  } > "$env"
  img=$(docker inspect -f '{{.Config.Image}}' "$PROD_GW")
  mkdir -p "$J/out"
  local rc=0
  docker run --rm --network "$NET" --env-file "$env" -v "$TREE":/wt:ro -v "$TREE":/app:ro -v "$H":/h:ro \
    -v "$J/out":/out -w /wt/backend/api_gateway "$img" python "/h/$1" "${@:2}" || rc=$?
  shred -u "$env"
  return $rc
}

jalankan() {
  local sk="${1:?skenario}" lengan="${2:-AB}"
  [ -f "$H/skenario_$sk.py" ] || galat "skenario tak ada: $H/skenario_$sk.py"
  docker inspect "$PG" >/dev/null 2>&1 || galat "$PG belum ada -- jalankan: mh-journey.sh siapkan"
  reset_db
  local uji; uji=$(jalan_py uji_harness.py 2>&1 | grep -v -i warning || true)
  echo "$uji" | grep -E "^(HIJAU|MERAH|tertutup|BOCOR|KONTROL|RINGKAS|HARNESS)"
  [ "$(echo "$uji" | tail -1)" = "HARNESS OK" ] || galat "uji harness TIDAK OK -- skenario tak dijalankan"
  local l gagal=0
  for l in $(echo "$lengan" | grep -o .); do
    reset_db
    rm -rf "$J/out/$sk/$l"
    echo "== skenario $sk lengan $l"
    local rc=0
    jalan_py jalan_skenario.py "$sk" "$l" 2>&1 | grep -E "^[0-9]{2} |PAGAR|RINGKAS|Traceback|Error" || true
    [ -f "$J/out/$sk/$l/RINGKAS.json" ] || { echo "lengan $l: RINGKAS tak ada (alat patah)"; return 2; }
    python3 -c "import json,sys; r=json.load(open(sys.argv[1])); sys.exit(1 if r['FAIL'] else 0)" \
      "$J/out/$sk/$l/RINGKAS.json" || gagal=1
  done
  [ $gagal = 0 ] && echo "LULUS: $sk [$lengan] 0 FAIL" || echo "GAGAL: $sk [$lengan] ada FAIL"
  return $gagal
}

case "${1:-}" in
  siapkan) siapkan ;;
  jalankan) shift; jalankan "$@" ;;
  status) docker ps -a --filter name="^$PG\$" --format '{{.Names}} {{.Status}}'; ls -la "$J" 2>/dev/null || true ;;
  bersihkan) docker rm -f "$PG" >/dev/null 2>&1 || true; docker network rm "$NET" >/dev/null 2>&1 || true
             find "$J" -type f -exec shred -u {} + 2>/dev/null || true; rm -rf "$J"; echo "bersih: $PG, $NET, $J dihapus" ;;
  *) sed -n '2,24p' "$0"; exit 2 ;;
esac
