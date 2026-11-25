#!/usr/bin/env bash
# MN-1: layanan stok_minimum_gudang melawan V511 di Postgres SEKALI-PAKAI (tabel tiruan minimal). Pemakaian: scripts/uji-mn1-pg.sh [pohon]
set -e
W=${1:-$(cd "$(dirname "$0")/.." && pwd)}; IMG=$(docker inspect -f '{{.Config.Image}}' milkyhoop-dev-postgres-1)
docker rm -f mn1pg >/dev/null 2>&1 || true; docker network create mn1net >/dev/null 2>&1 || true
docker run -d --rm --name mn1pg --network mn1net --memory 300m -e POSTGRES_PASSWORD=x $IMG >/dev/null
for i in $(seq 40); do docker exec mn1pg psql -h localhost -U postgres -c 'select 1' >/dev/null 2>&1 && break; sleep 1; done
docker exec -i mn1pg psql -U postgres -v ON_ERROR_STOP=1 -q <<'S'
CREATE TABLE products(id uuid primary key, tenant_id text, nama_produk text, base_unit text, satuan text, deleted_at timestamptz);
CREATE TABLE warehouses(id uuid primary key, tenant_id text, name text, is_active bool, is_system bool);
CREATE TABLE inventory_ledger(tenant_id text, product_id uuid, warehouse_id uuid, quantity_in numeric, quantity_out numeric);
CREATE TABLE tenant_features(tenant_id text, feature text, enabled bool);
S
docker exec -i mn1pg psql -U postgres -v ON_ERROR_STOP=1 -q < $W/backend/migrations/V511__stok_minimum_gudang.sql
set +e
docker run --rm --network mn1net -e DSN=postgresql://postgres:x@mn1pg/postgres -v $W/backend/api_gateway:/app/backend/api_gateway -v $W/scripts/uji/mn1_pg.py:/u.py:ro -e PYTHONPATH=/app/backend/api_gateway -w /app/backend/api_gateway --entrypoint python milkyhoop-dev-api_gateway:latest /u.py
rc=$?; docker rm -f mn1pg >/dev/null; docker network rm mn1net >/dev/null; exit $rc
