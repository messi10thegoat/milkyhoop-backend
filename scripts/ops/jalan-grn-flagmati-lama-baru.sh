#!/usr/bin/env bash
# Flag penerimaan_barang_v1 MATI: LAMA (pohon e5d8b712, pra-GRND) vs BARU (pohon utama LIVE) -- kaos + grapgrap-manado (izin pemilik), rollback-only.
set -u
LAMA=/root/mh-lama-e5d8; BARU=/root/milkyhoop-dev; H=$1
[ -d $LAMA ] || git -C $BARU worktree add -q --detach $LAMA e5d8b712
echo "LAMA $(git -C $LAMA rev-parse --short HEAD)  BARU $(git -C $BARU rev-parse --short HEAD)"
G=milkyhoop-dev-api_gateway; IMG=$(docker inspect $G --format "{{.Config.Image}}"); E=$(mktemp /root/.fm-XXXX); chmod 600 $E; D=$(mktemp -d /root/.fmd-XXXX)
printf "URL=%s\n" "$(docker exec $G printenv DATABASE_URL)" > $E; cp $H $D/f.py
jalan() { timeout 300 docker run --rm --network milkyhoop_dev_network --env-file $E -e TENANT=$2 -v $1/backend:/app/backend:ro -v $D/f.py:/h.py:ro \
  -w /app/backend/api_gateway --entrypoint python $IMG -I /h.py dump 2>&1; }
for t in kaos-biru-konveksi grapgrap-manado; do
  echo "=== $t"
  jalan $LAMA $t > $D/lama-$t.out; jalan $BARU $t > $D/baru-$t.out
  grep '^HASIL ' $D/lama-$t.out | sed 's/^HASIL //' > $D/lama.json; grep '^HASIL ' $D/baru-$t.out | sed 's/^HASIL //' > $D/baru.json
  if [ -s $D/lama.json ] && [ -s $D/baru.json ]; then python3 $D/f.py banding $D/lama.json $D/baru.json
  else echo "FAIL dump kosong"; tail -15 $D/lama-$t.out; tail -15 $D/baru-$t.out; fi
done
rm -rf $E $D
echo "sisa PO TES E2E FLAGMATI: $(docker exec -i milkyhoop-dev-postgres-1 psql -U postgres -d milkydb -At -c "SELECT count(*) FROM purchase_orders WHERE ref_no = 'TES E2E GRN FLAGMATI'") flag: $(docker exec -i milkyhoop-dev-postgres-1 psql -U postgres -d milkydb -At -c "SELECT count(*) FROM tenant_features WHERE feature = 'penerimaan_barang_v1'")"
echo SELESAI_SEMUA
