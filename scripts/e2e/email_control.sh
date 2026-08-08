#!/bin/bash
# KONTROL — perilaku master (tanpa perbaikan) di :8001. Menunjukkan kebohongannya.
B=http://localhost:8001/api
Q(){ local b=$(printf '%s' "$1"|base64); ssh root@159.89.202.160 "echo $b|base64 -d>/tmp/q.sql && docker cp /tmp/q.sql milkyhoop-dev-postgres-1:/tmp/q.sql>/dev/null && docker exec milkyhoop-dev-postgres-1 sh -c 'PGPASSWORD=Proyek771977 psql -U postgres -d milkydb -tA -f /tmp/q.sql'" 2>&1|tr -d '\r '; }
M="ctrl+$(date +%s)@kaosbiru.co.id"
N0=$(Q "SELECT count(*) FROM pending_registrations;")
C=$(curl -s -o /tmp/ec -w '%{http_code}' -X POST "$B/auth/signup/register" -H 'Content-Type: application/json' -d "{\"email\":\"$M\"}")
echo "  register -> HTTP $C"
echo "  badan    : $(cat /tmp/ec)"
echo "  baris pending: $N0 -> $(Q "SELECT count(*) FROM pending_registrations;")"
echo "  baris untuk $M: $(Q "SELECT count(*) FROM pending_registrations WHERE email='$M';")"
Q "DELETE FROM pending_registrations WHERE email LIKE 'ctrl+%';" >/dev/null
