#!/usr/bin/env bash
# AS-GP1 PO: tes terfokus (SEMUA pemanggil + teks-sumber + pemindai) + harness nyata kaos (rollback) baru/merah_idem/merah_penentu.
W=${W:-/root/mh-gp1}
cd $W && git fetch -q deploy && echo "HEAD $(git rev-parse --short HEAD) master $(git rev-parse --short deploy/master)" && (git merge-base --is-ancestor deploy/master HEAD && echo on-top || echo MASTER-MOVED)
L=/root/logs/gp1-$(date -u +%Y%m%dT%H%M%S); mkdir -p $L; echo "log lengkap: $L"
F=$(cd backend/api_gateway && grep -rlnE "update_purchase_order|delete_purchase_order|send_purchase_order|cancel_purchase_order|close_purchase_order|routers import purchase_orders|routers\.purchase_orders|from app.routers.purchase_orders|idem_buat" tests/unit --include=*.py | sort -u | tr "\n" " ")
echo "=== tes terfokus ($(echo $F | wc -w) berkas)"
timeout 900 scripts/jalankan_unit.sh $W -q -p no:cacheprovider $F tests/unit/test_pemindai_idem_ast.py tests/unit/test_pagar_rute_izin.py tests/unit/test_izin_rute_pratinjau.py \
  tests/unit/test_rute_endpoint_bukan_helper.py tests/unit/test_nomor_jurnal_satu_ruang.py tests/unit/test_idem_sumber_basi.py > $L/unit.log 2>&1
grep -E "^(FAILED|ERROR) |^=+ .* in " $L/unit.log | tail -15
G=milkyhoop-dev-api_gateway; IMG=$(docker inspect $G --format "{{.Config.Image}}"); E=$(mktemp /root/.g4-XXXX); chmod 600 $E
printf "URL=%s\n" "$(docker exec $G printenv DATABASE_URL)" > $E
for m in "" merah_idem; do
  echo "=== harness ${m:-baru}"
  timeout 600 docker run --rm --network milkyhoop_dev_network --env-file $E -v $W/backend:/app/backend:ro -v $W/scripts/ops/uji-nyata-as-gp1.py:/h.py:ro \
    -w /app/backend/api_gateway --entrypoint python $IMG -I /h.py baru $m > $L/h-${m:-baru}.log 2>&1 < /dev/null; echo "rc=$?"
  grep -vE "^PASS |^CONTOH |pydantic|PydanticSerial|return self.__pydantic" $L/h-${m:-baru}.log | tail -25; echo "PASS: $(grep -c "^PASS " $L/h-${m:-baru}.log)"
done
rm -f $E
echo "sisa PO TES E2E AS-GP1: $(docker exec -i milkyhoop-dev-postgres-1 psql -U postgres -d milkydb -At -c "SELECT count(*) FROM purchase_orders WHERE ref_no = 'TES E2E AS-GP1'" < /dev/null)"
echo SELESAI_SEMUA
