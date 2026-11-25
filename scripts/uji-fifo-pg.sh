#!/usr/bin/env bash
# FIFO unit 2: fungsi fifo_lapisan melawan V510 di Postgres SEKALI-PAKAI (tabel tiruan minimal). Pemakaian: scripts/uji-fifo-pg.sh [pohon]
set -e
W=${1:-$(cd "$(dirname "$0")/.." && pwd)}; IMG=$(docker inspect -f '{{.Config.Image}}' milkyhoop-dev-postgres-1)
docker rm -f fifopg >/dev/null 2>&1 || true; docker network create fifonet >/dev/null 2>&1 || true
docker run -d --rm --name fifopg --network fifonet --memory 400m -e POSTGRES_PASSWORD=x $IMG >/dev/null
for i in $(seq 40); do docker exec fifopg psql -h localhost -U postgres -c 'select 1' >/dev/null 2>&1 && break; sleep 1; done
docker exec -i fifopg psql -U postgres -v ON_ERROR_STOP=1 -q <<'S'
CREATE TABLE products(id uuid primary key, tenant_id text, costing_method text);
CREATE TABLE tenant_config(tenant_id text);
CREATE TABLE tenant_features(tenant_id text, feature text, enabled bool);
CREATE TABLE item_batches(id uuid primary key);
CREATE TABLE inventory_ledger(id uuid primary key);
CREATE TABLE galat(x int);
INSERT INTO products VALUES('00000000-0000-0000-0000-000000000001','t','fifo');
S
docker exec -i fifopg psql -U postgres -v ON_ERROR_STOP=1 -q < $W/backend/migrations/V510__fifo_lapisan.sql
set +e
docker run --rm --network fifonet -e DSN=postgresql://postgres:x@fifopg/postgres -v $W/backend/api_gateway:/app/backend/api_gateway -v $W/scripts/uji/fifo_pg.py:/u.py:ro -e PYTHONPATH=/app/backend/api_gateway -w /app/backend/api_gateway --entrypoint python milkyhoop-dev-api_gateway:latest /u.py
rc=$?; docker rm -f fifopg >/dev/null; docker network rm fifonet >/dev/null; exit $rc
