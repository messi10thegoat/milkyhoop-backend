#!/usr/bin/env bash
# Gerbang V328 (dashboard_task_state) di DB SCRATCH — tak pernah menyentuh milkydb. apply -1 dua kali
# (idempoten, seperti apply_mig), PK/CHECK/FK menolak baris buruk, RLS aktif, tipe text, rollback bersih.
set -uo pipefail
M="${1:?worktree}/backend/migrations"
P() { docker exec -i -e PGPASSWORD="$PGPASS" milkyhoop-dev-postgres-1 psql -U postgres -v ON_ERROR_STOP=1 -qAt "$@"; }
PGPASS="$(docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' milkyhoop-dev-postgres-1 | sed -n 's/^POSTGRES_PASSWORD=//p')"
gagal=0; cek() { if [ "$2" = "$3" ]; then echo "OK   $1"; else echo "GAGAL $1 (dapat '$2', harap '$3')"; gagal=$((gagal+1)); fi; }
P -d postgres -c "DROP DATABASE IF EXISTS scratch_v328" -c "CREATE DATABASE scratch_v328" 2>/dev/null
P -d scratch_v328 <<'SQL'
CREATE TABLE "Tenant"(id text PRIMARY KEY); CREATE TABLE "User"(id text PRIMARY KEY);
INSERT INTO "Tenant" VALUES ('t1'),('t2'); INSERT INTO "User" VALUES ('u1'),('u2');
SQL
for i in 1 2; do P -1 -d scratch_v328 < "$M/V328__dashboard_task_state.sql"; cek "apply -1 #$i" "$?" 0; done
P -d scratch_v328 <<'SQL'
INSERT INTO dashboard_task_state(tenant_id,user_id,task_key,state) VALUES ('t1','u1','ar_due_today:X','dismissed');
INSERT INTO dashboard_task_state(tenant_id,user_id,task_key,state) VALUES ('t1','u1','ar_due_today:X','dismissed') ON CONFLICT DO NOTHING;
INSERT INTO dashboard_task_state(tenant_id,user_id,task_key,state) VALUES ('t1','u2','ar_due_today:X','dismissed');
SQL
cek "PK per pengguna (2 baris)" "$(P -d scratch_v328 -c 'SELECT count(*) FROM dashboard_task_state')" 2
cek "RLS aktif" "$(P -d scratch_v328 -c "SELECT relrowsecurity FROM pg_class WHERE relname='dashboard_task_state'")" t
cek "tipe text" "$(P -d scratch_v328 -c "SELECT string_agg(data_type,',' ORDER BY column_name) FROM information_schema.columns WHERE table_name='dashboard_task_state' AND column_name IN ('tenant_id','user_id')")" "text,text"
for bad in "('t1','u1','k2','done')" "('tX','u1','k3','dismissed')" "('t1','uX','k4','dismissed')" "('t1','u1','','dismissed')"; do
  P -d scratch_v328 -c "INSERT INTO dashboard_task_state(tenant_id,user_id,task_key,state) VALUES $bad" >/dev/null 2>&1
  cek "tolak $bad" "$?" 1
done
P -1 -d scratch_v328 < "$M/V328__dashboard_task_state_ROLLBACK.sql"
cek "rollback menghapus tabel" "$(P -d scratch_v328 -c "SELECT to_regclass('dashboard_task_state') IS NULL")" t
P -d postgres -c "DROP DATABASE scratch_v328"
echo "GAGAL=$gagal"; exit $((gagal > 0))
