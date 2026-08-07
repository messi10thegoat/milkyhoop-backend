#!/bin/bash
# Uji dua arah V223 — verdict dari EXIT CODE psql, bukan dari \echo.
# (\echo di psql tercetak tanpa syarat; versi pertama skrip ini melaporkan
#  "GAGAL" untuk uji yang justru LULUS. Alat diperbaiki, bukan dibaca-akali.)
PASS=0; FAIL=0
run(){ # $1=label $2=sql $3=harus: tolak|terima
  local b=$(printf 'BEGIN; %s ROLLBACK;' "$2" | base64)
  ssh root@159.89.202.160 "echo $b | base64 -d > /tmp/t.sql && docker cp /tmp/t.sql milkyhoop-dev-postgres-1:/tmp/t.sql >/dev/null && docker exec milkyhoop-dev-postgres-1 sh -c 'PGPASSWORD=Proyek771977 psql -v ON_ERROR_STOP=1 -q -U postgres -d milkydb -f /tmp/t.sql'" >/dev/null 2>&1
  local rc=$?
  local got=$([ $rc -eq 0 ] && echo terima || echo tolak)
  if [ "$got" = "$3" ]; then echo "  ✓ $1 -> $got (harap $3)"; PASS=$((PASS+1));
  else echo "  !! $1 -> $got, HARAP $3"; FAIL=$((FAIL+1)); fi
}
echo "=== UJI DUA ARAH V223 (verdict = exit code) ==="
run "INACTIVE"  "UPDATE user_tenant_roles SET status='INACTIVE';"  tolak
run "active"    "UPDATE user_tenant_roles SET status='active';"    tolak
run "SUSPENDED" "UPDATE user_tenant_roles SET status='SUSPENDED';" terima
run "ACTIVE"    "UPDATE user_tenant_roles SET status='ACTIVE';"    terima
echo "===== $PASS sesuai, $FAIL menyimpang ====="
[ $FAIL -eq 0 ]
