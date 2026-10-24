#!/usr/bin/env bash
# SALDO-AWAL-VENDOR-C: tes terfokus (pemanggil set_vendor_opening_balance + teks-sumber + pemindai) + sabotase salinan (penahan MATI, WAJIB merah)
# + harness ROLLBACK kaos baru/merah_c (penahan MATI di salinan pohon -> WAJIB merah).
W=${W:-/root/mh-vobc}
cd $W && git fetch -q deploy && echo "HEAD $(git rev-parse --short HEAD) master $(git rev-parse --short deploy/master)" && (git merge-base --is-ancestor deploy/master HEAD && echo on-top || echo MASTER-MOVED)
L=/root/logs/vobc-$(date -u +%Y%m%dT%H%M%S); mkdir -p $L; echo "log lengkap: $L"
F=$(cd backend/api_gateway && grep -rlnE "set_vendor_opening_balance|vendors as RV|routers import vendors|routers\.vendors|VENDOR_OPENING" tests/unit --include=*.py | sort -u | tr "\n" " ")
echo "=== tes terfokus ($(echo $F | wc -w) berkas)"
timeout 600 scripts/jalankan_unit.sh $W -q -p no:cacheprovider $F tests/unit/test_saldo_awal_vendor_c.py tests/unit/test_t10b3b_tulis_tanggal_bisnis.py tests/unit/test_v456_v457_sql.py tests/unit/test_pemindai_idem_ast.py \
  tests/unit/test_pagar_rute_izin.py tests/unit/test_izin_rute_pratinjau.py tests/unit/test_rute_endpoint_bukan_helper.py > $L/unit.log 2>&1
grep -E "^(FAILED|ERROR) |^=+ .* in " $L/unit.log | tail -10
echo "=== sabotase If-Match sebelum replay (WAJIB merah)"
P=$(mktemp -d /root/.sabvobc-XXXX); mkdir -p $P/backend
rsync -a --exclude .pytest_cache --exclude __pycache__ backend/api_gateway $P/backend/ && ln -s $W/backend/migrations $P/backend/migrations
if python3 scripts/ops/sabotase-saldo-awal-vendor-c.py $P/backend/api_gateway; then
  timeout 300 scripts/jalankan_unit.sh $P -q -p no:cacheprovider tests/unit/test_saldo_awal_vendor_c.py 2>&1 | grep -E "^(FAILED|ERROR) |^=+ .* in " | tail -5
else echo "FAIL sabotase tak terpasang"; fi
rm -rf $P
G=milkyhoop-dev-api_gateway; IMG=$(docker inspect $G --format "{{.Config.Image}}"); E=$(mktemp /root/.vobc-XXXX); chmod 600 $E
printf "URL=%s\n" "$(docker exec $G printenv DATABASE_URL)" > $E
for m in "" merah_c; do
  echo "=== harness ${m:-baru} (ROLLBACK kaos)"; POHON=$W
  if [ -n "$m" ]; then POHON=$(mktemp -d /root/.sabvobc-XXXX); mkdir -p $POHON/backend
    rsync -a --exclude .pytest_cache --exclude __pycache__ backend/api_gateway $POHON/backend/ && ln -s $W/backend/migrations $POHON/backend/migrations
    python3 scripts/ops/sabotase-saldo-awal-vendor-c.py $POHON/backend/api_gateway || { echo "FAIL sabotase tak terpasang"; rm -rf $POHON; continue; }; fi
  timeout 300 docker run --rm --network milkyhoop_dev_network --env-file $E -v $POHON/backend:/app/backend:ro -v $W/scripts/ops/uji-nyata-saldo-awal-vendor-c.py:/h.py:ro \
    -w /app/backend/api_gateway --entrypoint python $IMG -I /h.py $m > $L/h-${m:-baru}.log 2>&1 < /dev/null; echo "rc=$?"
  grep -vE "^PASS |pydantic|PydanticSerial|return self.__pydantic" $L/h-${m:-baru}.log | tail -12; echo "PASS: $(grep -c "^PASS " $L/h-${m:-baru}.log)"
  [ "$POHON" != "$W" ] && rm -rf $POHON
done
rm -f $E
echo SELESAI_SEMUA
