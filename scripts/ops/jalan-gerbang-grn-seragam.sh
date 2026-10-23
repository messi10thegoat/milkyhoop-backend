#!/usr/bin/env bash
# GRN SERAGAM: tes terfokus + harness nyata kaos (rollback) baru/merah/merah_grni. Log lengkap /root/logs/grn-seragam-*.
W=${W:-/root/mh-grnseragam}
cd $W && git fetch -q deploy && echo "HEAD $(git rev-parse --short HEAD) master $(git rev-parse --short deploy/master)" && (git merge-base --is-ancestor deploy/master HEAD && echo on-top || echo MASTER-MOVED)
L=/root/logs/grn-seragam-$(date -u +%Y%m%dT%H%M%S); mkdir -p $L; echo "log lengkap: $L"
echo "=== tes terfokus"
F=$(cd backend/api_gateway && grep -rln "goods_receipts\|grn_daftar\|GR\.\|list_goods_receipts\|get_goods_receipt" tests/unit --include=*.py | tr "\n" " ")
timeout 600 scripts/jalankan_unit.sh $W -q -p no:cacheprovider $F tests/unit/test_grn_seragam.py tests/unit/test_pagar_rute_izin.py \
  tests/unit/test_izin_rute_pratinjau.py tests/unit/test_rute_endpoint_bukan_helper.py tests/unit/test_nomor_jurnal_satu_ruang.py > $L/unit.log 2>&1
grep -E "^(FAILED|ERROR) |^=+ .* in " $L/unit.log | tail -12
G=milkyhoop-dev-api_gateway; IMG=$(docker inspect $G --format "{{.Config.Image}}"); E=$(mktemp /root/.gs-XXXX); chmod 600 $E
printf "URL=%s\n" "$(docker exec $G printenv DATABASE_URL)" > $E
for m in "" merah merah_grni; do
  echo "=== harness ${m:-baru}"
  timeout 600 docker run --rm --network milkyhoop_dev_network --env-file $E -v $W/backend:/app/backend:ro -v $W/scripts/ops/uji-nyata-grn-seragam.py:/h.py:ro \
    -w /app/backend/api_gateway --entrypoint python $IMG -I /h.py baru $m > $L/h-${m:-baru}.log 2>&1 < /dev/null; echo "rc=$?"
  grep -vE "^PASS |pydantic|PydanticSerial|return self.__pydantic" $L/h-${m:-baru}.log | tail -25; echo "PASS: $(grep -c "^PASS " $L/h-${m:-baru}.log)"
done
rm -f $E
echo "sisa PO TES E2E GRN SERAGAM: $(docker exec -i milkyhoop-dev-postgres-1 psql -U postgres -d milkydb -At -c "SELECT count(*) FROM purchase_orders WHERE ref_no = 'TES E2E GRN SERAGAM'" < /dev/null)"
echo SELESAI_SEMUA
