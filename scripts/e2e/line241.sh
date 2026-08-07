#!/bin/bash
# Apakah baris 241 (fallback subscription_role) masih bisa DICAPAI pasca B/C?
# Skenario: anggota DIHAPUS dari tim (baris user_tenant_roles hilang) sementara
# tokennya masih hidup. business_role_id kosong BUKAN karena SUSPENDED.
B=http://localhost:8002/api; PW='KaosBiru2026!'
Q(){ local b=$(printf '%s' "$1" | base64); ssh root@159.89.202.160 "echo $b | base64 -d > /tmp/q.sql && docker cp /tmp/q.sql milkyhoop-dev-postgres-1:/tmp/q.sql >/dev/null && docker exec milkyhoop-dev-postgres-1 sh -c 'PGPASSWORD=Proyek771977 psql -U postgres -d milkydb -tA -f /tmp/q.sql'" 2>&1|tr -d '\r '; }
scp -q "$(dirname "$0")/deact_setup.sql" root@159.89.202.160:/tmp/deact_setup.sql
ssh root@159.89.202.160 'docker cp /tmp/deact_setup.sql milkyhoop-dev-postgres-1:/tmp/ >/dev/null && docker exec milkyhoop-dev-postgres-1 sh -c "PGPASSWORD=Proyek771977 psql -U postgres -d milkydb -q -f /tmp/deact_setup.sql"' >/dev/null
sleep 6
KT=$(curl -s -X POST "$B/auth/login" -H 'Content-Type: application/json' -d "{\"email\":\"deact+kasir@kaosbiru.co.id\",\"password\":\"$PW\"}" | python3 -c 'import sys,json;print(json.load(sys.stdin).get("data",{}).get("access_token") or "")')
[ -z "$KT" ] && { echo "!! LOGIN GAGAL = KEGAGALAN ALAT"; exit 2; }
echo "plan tier user ini: $(Q "SELECT role FROM \"User\" WHERE email='deact+kasir@kaosbiru.co.id';")"
echo "SEBELUM dihapus  /sales-invoices -> $(curl -s -o /dev/null -w '%{http_code}' "$B/sales-invoices" -H "Authorization: Bearer $KT")"
Q "DELETE FROM user_tenant_roles utr USING \"User\" u WHERE u.id::uuid=utr.user_id AND u.email='deact+kasir@kaosbiru.co.id';" >/dev/null
echo "baris peran tersisa: $(Q "SELECT count(*) FROM user_tenant_roles utr JOIN \"User\" u ON u.id::uuid=utr.user_id WHERE u.email='deact+kasir@kaosbiru.co.id';")"
echo "SESUDAH dihapus  /sales-invoices -> $(curl -s -o /dev/null -w '%{http_code}' "$B/sales-invoices" -H "Authorization: Bearer $KT")"
echo "SESUDAH dihapus  /dashboard/all  -> $(curl -s -o /dev/null -w '%{http_code}' "$B/dashboard/all" -H "Authorization: Bearer $KT")"
Q "DELETE FROM \"User\" WHERE email LIKE 'deact+%';" >/dev/null
