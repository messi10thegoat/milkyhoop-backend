#!/bin/bash
# PROBE catatan-2: rute yang MELEWATI PermissionMiddleware (skip patterns).
# Pertanyaan: anggota SUSPENDED dengan TOKEN LAMA masih bisa apa?
B=${B:-http://localhost:8001/api}
PW='KaosBiru2026!'
rm -f /tmp/sd_abort
Q(){ local b=$(printf '%s' "$1" | base64); ssh root@159.89.202.160 "echo $b | base64 -d > /tmp/q.sql && docker cp /tmp/q.sql milkyhoop-dev-postgres-1:/tmp/q.sql >/dev/null && docker exec milkyhoop-dev-postgres-1 sh -c 'PGPASSWORD=Proyek771977 psql -U postgres -d milkydb -tA -f /tmp/q.sql'" 2>&1 | tr -d '\r' | tr -d ' '; }
tok(){ sleep 6; local r=$(curl -s -X POST "$B/auth/login" -H 'Content-Type: application/json' -d "{\"email\":\"$1\",\"password\":\"$PW\"}"); printf '%s' "$r" | python3 -c 'import sys,json
try: print(json.load(sys.stdin).get("data",{}).get("access_token") or "")
except Exception: print("")'; }

HZ=$(curl -s -o /dev/null -w '%{http_code}' "${B%/api}/healthz")
[ "$HZ" = "200" ] || { echo "!! healthz=$HZ — TIDAK ADA HASIL SAH"; exit 2; }
echo "PREFLIGHT: gateway hidup ($HZ)"

scp -q "$(dirname "$0")/deact_setup.sql" root@159.89.202.160:/tmp/deact_setup.sql
ssh root@159.89.202.160 'docker cp /tmp/deact_setup.sql milkyhoop-dev-postgres-1:/tmp/ >/dev/null && docker exec milkyhoop-dev-postgres-1 sh -c "PGPASSWORD=Proyek771977 psql -U postgres -d milkydb -q -f /tmp/deact_setup.sql"' | tail -2

KT=$(tok deact+kasir@kaosbiru.co.id)
[ -z "$KT" ] && { echo "!! GAGAL LOGIN — KEGAGALAN ALAT, bukan hasil"; exit 2; }
echo "token anggota diterbitkan (SEBELUM suspend) — token INI dipakai seterusnya"

probe(){ # $1=label $2=path
  local code=$(curl -s -o /tmp/sd_body -w '%{http_code}' "$B$2" -H "Authorization: Bearer $KT")
  echo "    [$1] GET $2 -> HTTP $code"
  head -c 220 /tmp/sd_body; echo
}

echo; echo "=== SEBELUM suspend (ACTIVE) ==="
probe ACTIVE /dashboard/all
probe ACTIVE /permissions/me
probe ACTIVE /sales-invoices

echo; echo "=== SUSPEND di DB ==="
Q "UPDATE user_tenant_roles utr SET status='SUSPENDED' FROM \"User\" u WHERE u.id::uuid=utr.user_id AND u.email='deact+kasir@kaosbiru.co.id';" >/dev/null
echo "    status: $(Q "SELECT utr.status FROM user_tenant_roles utr JOIN \"User\" u ON u.id::uuid=utr.user_id WHERE u.email='deact+kasir@kaosbiru.co.id';")"

echo; echo "=== SESUDAH suspend — TOKEN YANG SAMA ==="
probe SUSPENDED /dashboard/all
probe SUSPENDED /permissions/me
probe SUSPENDED /sales-invoices
