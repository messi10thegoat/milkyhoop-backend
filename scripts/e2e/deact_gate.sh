#!/bin/bash
# GATE deactivate/reactivate + PolicyEngine-baca-status + pagar dashboard.
# Menegaskan keadaan PASCA-FIX. Dijalankan di :8002 (harus HIJAU penuh) dan
# di :8001 (harus MERAH di butir inti — bukti gate ini bisa BICARA).
B=${B:-http://localhost:8002/api}
PW='KaosBiru2026!'; LOGIN_GAP=${LOGIN_GAP:-6}
PASS=0; FAIL=0; rm -f /tmp/dg_abort
ok(){ if [ "$2" = "$3" ]; then echo "  ✓ $1: $2"; PASS=$((PASS+1));
      else echo "  ✗ $1: dapat=$2 HARAP=$3"; FAIL=$((FAIL+1)); fi; }
Q(){ local b=$(printf '%s' "$1" | base64); ssh root@159.89.202.160 "echo $b | base64 -d > /tmp/q.sql && docker cp /tmp/q.sql milkyhoop-dev-postgres-1:/tmp/q.sql >/dev/null && docker exec milkyhoop-dev-postgres-1 sh -c 'PGPASSWORD=Proyek771977 psql -U postgres -d milkydb -tA -f /tmp/q.sql'" 2>&1 | tr -d '\r' | tr -d ' '; }
tok(){ sleep "$LOGIN_GAP"; local t=$(curl -s -X POST "$B/auth/login" -H 'Content-Type: application/json' -d "{\"email\":\"$1\",\"password\":\"$PW\"}" | python3 -c 'import sys,json
try: print(json.load(sys.stdin).get("data",{}).get("access_token") or "")
except Exception: print("")'); [ -z "$t" ] && { echo "!! GAGAL LOGIN $1 = KEGAGALAN ALAT" >&2; touch /tmp/dg_abort; }; printf '%s' "$t"; }
abort(){ [ -f /tmp/dg_abort ] && { echo; echo "===== DIHENTIKAN: kegagalan alat, TIDAK ADA HASIL SAH ====="; exit 2; }; }
code(){ curl -s -o /dev/null -w '%{http_code}' "$@"; }

HZ=$(curl -s -o /dev/null -w '%{http_code}' "${B%/api}/healthz")
[ "$HZ" = "200" ] || { echo "!! healthz=$HZ di ${B%/api} — TIDAK ADA HASIL SAH"; exit 2; }
echo "PREFLIGHT: $B hidup ($HZ)"

scp -q "$(dirname "$0")/deact_setup.sql" root@159.89.202.160:/tmp/deact_setup.sql
ssh root@159.89.202.160 'docker cp /tmp/deact_setup.sql milkyhoop-dev-postgres-1:/tmp/ >/dev/null && docker exec milkyhoop-dev-postgres-1 sh -c "PGPASSWORD=Proyek771977 psql -U postgres -d milkydb -q -f /tmp/deact_setup.sql"' >/dev/null
echo "fixture: anggota CASHIER ACTIVE dibuat ulang"

OT=$(tok owner@kaosbiru.co.id); abort
KT=$(tok deact+kasir@kaosbiru.co.id); abort     # <-- TOKEN INI dipakai sebelum & sesudah suspend
MID=$(Q "SELECT utr.id FROM user_tenant_roles utr JOIN \"User\" u ON u.id::uuid=utr.user_id WHERE u.email='deact+kasir@kaosbiru.co.id';")
OMID=$(Q "SELECT utr.id FROM user_tenant_roles utr JOIN \"User\" u ON u.id::uuid=utr.user_id WHERE u.email='owner@kaosbiru.co.id';")
[ -z "$MID" ] || [ -z "$OMID" ] && { echo "!! member_id tak terbaca = KEGAGALAN ALAT"; exit 2; }

echo; echo "--- PAGAR: keadaan sehat harus TETAP jalan (uji dua arah) ---"
ok "owner role_code" "$(curl -s "$B/permissions/me" -H "Authorization: Bearer $OT" | python3 -c 'import sys,json;print(json.load(sys.stdin).get("role_code"))')" "OWNER"
ok "owner /dashboard/all" "$(code "$B/dashboard/all" -H "Authorization: Bearer $OT")" "200"
ok "anggota AKTIF /dashboard/all" "$(code "$B/dashboard/all" -H "Authorization: Bearer $KT")" "200"
ok "anggota AKTIF /sales-invoices" "$(code "$B/sales-invoices" -H "Authorization: Bearer $KT")" "200"

echo; echo "--- GUARD: yang TIDAK boleh dinonaktifkan ---"
ok "OWNER ditolak mutlak" "$(code -X PATCH "$B/team-members/$OMID/deactivate" -H "Authorization: Bearer $OT")" "403"
ok "owner nonaktifkan diri sendiri" "$(code -X PATCH "$B/team-members/$OMID/deactivate" -H "Authorization: Bearer $OT")" "403"

echo; echo "--- INTI: deactivate lalu TOKEN YANG SAMA ---"
ok "PATCH deactivate" "$(code -X PATCH "$B/team-members/$MID/deactivate" -H "Authorization: Bearer $OT")" "200"
ok "status di DB" "$(Q "SELECT status FROM user_tenant_roles WHERE id='$MID';")" "SUSPENDED"
ok "★ token SAMA /sales-invoices" "$(code "$B/sales-invoices" -H "Authorization: Bearer $KT")" "403"
ok "★ token SAMA /dashboard/all" "$(code "$B/dashboard/all" -H "Authorization: Bearer $KT")" "403"
ok "token SAMA /permissions/me" "$(code "$B/permissions/me" -H "Authorization: Bearer $KT")" "403"

echo; echo "--- PULIH: reactivate mengembalikan akses ---"
ok "PATCH reactivate" "$(code -X PATCH "$B/team-members/$MID/reactivate" -H "Authorization: Bearer $OT")" "200"
ok "status di DB" "$(Q "SELECT status FROM user_tenant_roles WHERE id='$MID';")" "ACTIVE"
ok "token SAMA pulih /sales-invoices" "$(code "$B/sales-invoices" -H "Authorization: Bearer $KT")" "200"
ok "token SAMA pulih /dashboard/all" "$(code "$B/dashboard/all" -H "Authorization: Bearer $KT")" "200"

echo; echo "===== $PASS sesuai, $FAIL menyimpang ====="
[ $FAIL -eq 0 ]
