#!/bin/bash
# Membuat anggota CASHIER NYATA lewat alur undangan penuh (bukan seed DB).
B=http://localhost:8002/api; PW='KaosBiru2026!'; M='kasir.uji@kaosbiru.co.id'
Q(){ local b=$(printf '%s' "$1"|base64); ssh root@159.89.202.160 "echo $b|base64 -d>/tmp/q.sql && docker cp /tmp/q.sql milkyhoop-dev-postgres-1:/tmp/q.sql>/dev/null && docker exec milkyhoop-dev-postgres-1 sh -c 'PGPASSWORD=Proyek771977 psql -U postgres -d milkydb -tA -f /tmp/q.sql'" 2>&1|tr -d '\r '; }
Q "DELETE FROM user_tenant_roles utr USING \"User\" u WHERE u.id::uuid=utr.user_id AND u.email='$M';
   DELETE FROM \"User\" WHERE email='$M'; DELETE FROM team_invitations WHERE email='$M';" >/dev/null
sleep 12
OT=$(curl -s -X POST "$B/auth/login" -H 'Content-Type: application/json' -d "{\"email\":\"delivered+owner@resend.dev\",\"password\":\"$PW\"}"|python3 -c 'import sys,json;print(json.load(sys.stdin).get("data",{}).get("access_token") or "")')
[ -z "$OT" ] && { echo "!! login owner gagal"; exit 2; }
RID=$(Q "SELECT id FROM roles WHERE code='CASHIER' LIMIT 1;")
R=$(curl -s -X POST "$B/team-members/invite" -H "Authorization: Bearer $OT" -H 'Content-Type: application/json' -d "{\"email\":\"$M\",\"role_id\":\"$RID\",\"name\":\"Kasir Uji\"}")
LINK=$(printf '%s' "$R"|python3 -c 'import sys,json;print(json.load(sys.stdin).get("data",{}).get("invite_link",""))')
echo "link undangan: $LINK"
TOK="${LINK##*/}"
curl -s -X POST "$B/invite/$TOK/accept" -H 'Content-Type: application/json' -d "{\"name\":\"Kasir Uji\",\"password\":\"$PW\",\"password_confirm\":\"$PW\"}" | head -c 120; echo
echo "peran di DB: $(Q "SELECT r.code||' '||utr.status FROM user_tenant_roles utr JOIN \"User\" u ON u.id::uuid=utr.user_id JOIN roles r ON r.id=utr.role_id WHERE u.email='$M';")"
