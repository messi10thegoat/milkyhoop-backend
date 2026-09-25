#!/usr/bin/env bash
# Gerbang V310: flag CW kaos-saja. DB scratch milkydb_v310 = skema + "Tenant" + tenant_features prod
# (TIDAK menulis ke milkydb). Terapkan V310 DUA kali (idempoten) lalu periksa kaos 2 / grapgrap 0.
#   gate_v310_flag.sh <V310.sql>   -> LULUS exit 0 ;  tanpa argumen -> KONTROL MERAH (harus GAGAL)
set -uo pipefail
P=milkyhoop-dev-postgres-1; DB=milkydb_v310
q() { docker exec "$P" psql -U postgres "$@"; }
q -d postgres -qc "DROP DATABASE IF EXISTS $DB" -c "CREATE DATABASE $DB" >/dev/null 2>&1 || exit 2
docker exec "$P" sh -c "pg_dump -U postgres -s milkydb | psql -U postgres -d $DB -q -o /dev/null" 2>/dev/null
docker exec "$P" sh -c "pg_dump -U postgres --data-only -t 'public.\"Tenant\"' -t public.tenant_features milkydb | psql -U postgres -d $DB -q -o /dev/null" || exit 2
if [ -n "${1:-}" ]; then
  docker cp "$1" "$P:/tmp/v310.sql"
  for i in 1 2; do q -d "$DB" -v ON_ERROR_STOP=1 -q -1 -f /tmp/v310.sql >/tmp/gate_v310_$i.log 2>&1 || { echo "GAGAL: migrasi putaran $i"; tail -3 /tmp/gate_v310_$i.log; q -d postgres -qc "DROP DATABASE IF EXISTS $DB"; exit 1; }; done
fi
k=$(q -d "$DB" -Atc "select count(*) from tenant_features where tenant_id='kaos-biru-konveksi' and enabled and feature in ('conversational_detail_so','conversational_form_so_save')")
g=$(q -d "$DB" -Atc "select count(*) from tenant_features where tenant_id<>'kaos-biru-konveksi' and feature in ('conversational_detail_so','conversational_form_so_save')")
lama=$(q -d "$DB" -Atc "select count(*) from tenant_features where feature in ('conversational_form_so','conversational_workspace_so')")
q -d postgres -qc "DROP DATABASE IF EXISTS $DB"
echo "kaos=$k lain=$g flag_lama=$lama"
[ "$k" = "2" ] && [ "$g" = "0" ] && [ "$lama" = "4" ] && { echo "LULUS: V310 kaos 2, tenant lain 0, idempoten, flag lama utuh"; exit 0; }
echo "GAGAL"; exit 1
