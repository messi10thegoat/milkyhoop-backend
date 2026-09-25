#!/bin/bash
# Uji kering mh-rotasi-postgres.sh DUA ARAH di postgres scratch (tak menyentuh prod).
set -uo pipefail
D=/tmp/rotdry-pg; S=$(cd "$(dirname "$0")" && pwd)/mh-rotasi-postgres.sh
docker rm -f rotdry-pg >/dev/null 2>&1; docker network rm rotdry-net >/dev/null 2>&1
rm -rf $D; mkdir -p $D/bk; chmod 700 $D; cd $D
P0=$(openssl rand -hex 6)   # 12 karakter, seperti prod
printf 'DB_USER=postgres\nDB_PASSWORD=%s\nDB_NAME=milkydb\n' "$P0" > .env; chmod 600 .env
printf 'POSTGRES_PASSWORD=%s\nPOSTGRES_DB=milkydb\n' "$P0" > init.env; chmod 600 init.env
docker network create rotdry-net >/dev/null
docker run -d --name rotdry-pg --network rotdry-net --env-file init.env pgvector/pgvector:pg14 >/dev/null
for i in $(seq 1 60); do docker exec rotdry-pg pg_isready -U postgres -d milkydb >/dev/null 2>&1 && break; sleep 1; done
sleep 3
docker exec rotdry-pg sh -c 'grep -vE "^#|^$" $PGDATA/pg_hba.conf | tail -1'
docker exec rotdry-pg psql -U postgres -d milkydb -qc "create table t(x int); insert into t values (1),(2),(3)"
export ROT_DIR=$D ROT_PG_CTR=rotdry-pg ROT_NET=rotdry-net ROT_PG_HOST=rotdry-pg ROT_BACKUPS=$D/bk ROT_SKIP_SERVICES=1
sid() { grep -E '^DB_PASSWORD=' $D/.env | cut -d= -f2- | tr -d '\n' | sha256sum | cut -c1-12; }
echo "### A. MAJU"; A0=$(sid); bash $S; echo "rc=$?"; A1=$(sid)
echo "   .env sidik sebelum $A0 sesudah $A1 (harus beda)"
echo "   baris data: $(docker exec rotdry-pg psql -U postgres -d milkydb -Atc 'select count(*) from t')"
echo "### B. ROLLBACK (ROT_FORCE_FAIL)"; B0=$(sid); ROT_FORCE_FAIL=1 bash $S; echo "rc=$? (harus 1)"; B1=$(sid)
echo "   .env sidik sebelum $B0 sesudah $B1 (harus SAMA)"
echo "   sandi .env masih diterima TCP: $(docker run --rm --network rotdry-net -e PGPASSWORD="$(grep -E '^DB_PASSWORD=' .env | cut -d= -f2-)" pgvector/pgvector:pg14 psql -h rotdry-pg -U postgres -d milkydb -Atqc 'select 1' 2>/dev/null)"
echo "### C. REFUSE: .env tanpa DB_PASSWORD"; cp .env env.ok; grep -v '^DB_PASSWORD=' env.ok > .env; bash $S; echo "rc=$? (harus 3)"; cp env.ok .env
echo "### beres-beres"; docker rm -f rotdry-pg >/dev/null; docker network rm rotdry-net >/dev/null; rm -rf $D
