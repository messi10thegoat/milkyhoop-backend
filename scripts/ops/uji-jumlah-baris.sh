#!/bin/bash
# Ukur nyata JUMLAH-BARIS: scratch DB dari dump terbaru + V427; harness handler asli, tx ROLLBACK. Nol tulis prod.
set -uo pipefail
REPO=${REPO:-/root/mh-jumlah-baris}
PGC=milkyhoop-dev-postgres-1; DB=scratch_jb_$$
DUMP=$(ls -t /root/backups/milkydb-pre-*.dump | head -1)
docker exec $PGC psql -U postgres -d postgres -qc "CREATE DATABASE $DB" || exit 2
trap "docker exec $PGC psql -U postgres -d postgres -qc \"DROP DATABASE IF EXISTS $DB\" >/dev/null 2>&1" EXIT
docker exec -i $PGC pg_restore -U postgres -d $DB --no-owner < "$DUMP" > /tmp/jb_restore.log 2>&1
docker exec -i $PGC psql -U postgres -d $DB -v ON_ERROR_STOP=1 -q < ${MIG:-$REPO/backend/migrations/V427__jumlah_baris.sql} || exit 3
URLASLI=$(docker exec milkyhoop-dev-api_gateway printenv DATABASE_URL)
URL=$(echo "$URLASLI" | sed -E "s#/[^/?]+(\?.*)?\$#/$DB\1#")
docker run --rm --network milkyhoop_dev_network -e URL="$URL" \
  -e PYTHONPATH=/repo:/repo/backend/api_gateway/app:/repo/backend/api_gateway/libs:/repo/backend/services \
  -v $REPO:/repo:ro -v /root/jb_harness.py:/h/h.py:ro milkyhoop-dev-api_gateway:latest python /h/h.py
echo "rc=$?"
