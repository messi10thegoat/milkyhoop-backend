#!/usr/bin/env bash
# BELUM-DIFAKTUR: tes terfokus (SEMUA pemanggil so_aksi/rute SO + teks-sumber + pemindai) + sabotase U1..U4 (salinan, WAJIB merah)
# + harness READ ONLY baru/merah_dp/merah_filter (merah = kode NYATA dari salinan pohon yang disabotase).
W=${W:-/root/mh-bdf}
cd $W && git fetch -q deploy && echo "HEAD $(git rev-parse --short HEAD) master $(git rev-parse --short deploy/master)" && (git merge-base --is-ancestor deploy/master HEAD && echo on-top || echo MASTER-MOVED)
L=/root/logs/bdf-$(date -u +%Y%m%dT%H%M%S); mkdir -p $L; echo "log lengkap: $L"
F=$(cd backend/api_gateway && grep -rlnE "so_aksi|sales_orders as SO|routers import sales_orders|routers\.sales_orders|list_sales_orders|get_sales_order_summary|get_sales_order_detail|SalesOrderSummary|SalesOrderListItem|SalesOrderDetail|proforma_terbayar|ringkasan_pesanan" tests/unit --include=*.py | sort -u | tr "\n" " ")
echo "=== tes terfokus ($(echo $F | wc -w) berkas)"
timeout 900 scripts/jalankan_unit.sh $W -q -p no:cacheprovider $F tests/unit/test_so_belum_difaktur.py tests/unit/test_pemindai_idem_ast.py tests/unit/test_pagar_rute_izin.py \
  tests/unit/test_izin_rute_pratinjau.py tests/unit/test_rute_endpoint_bukan_helper.py tests/unit/test_nomor_jurnal_satu_ruang.py tests/unit/test_idem_sumber_basi.py \
  tests/unit/test_t10b_tanggal_bisnis_arap.py > $L/unit.log 2>&1
grep -E "^(FAILED|ERROR) |^=+ .* in " $L/unit.log | tail -15
salin() { POHON=$(mktemp -d /root/.sabbdf-XXXX); mkdir -p $POHON/backend
  rsync -a --exclude .pytest_cache --exclude __pycache__ backend/api_gateway $POHON/backend/ && ln -s $W/backend/migrations $POHON/backend/migrations
  python3 scripts/ops/sabotase-belum-difaktur.py $POHON/backend/api_gateway $1; }
TES_U="tests/unit/test_so_belum_difaktur.py tests/unit/test_pill_aksi_so.py"
for s in U1 U2 U3 U4; do
  echo "=== sabotase $s (WAJIB merah)"
  if salin $s; then timeout 300 scripts/jalankan_unit.sh $POHON -q -p no:cacheprovider $TES_U 2>&1 | grep -E "^(FAILED|ERROR) |^=+ .* in " | tail -6
  else echo "FAIL sabotase $s tak terpasang"; fi
  rm -rf $POHON
done
G=milkyhoop-dev-api_gateway; IMG=$(docker inspect $G --format "{{.Config.Image}}"); E=$(mktemp /root/.bdf-XXXX); chmod 600 $E
printf "URL=%s\n" "$(docker exec $G printenv DATABASE_URL)" > $E
for m in "" merah_dp merah_filter; do
  echo "=== harness ${m:-baru} (READ ONLY)"; POHON=$W
  if [ -n "$m" ]; then salin $m || { echo "FAIL sabotase tak terpasang"; rm -rf $POHON; continue; }; fi
  timeout 600 docker run --rm --network milkyhoop_dev_network --env-file $E -v $POHON/backend:/app/backend:ro -v $W/scripts/ops/uji-nyata-belum-difaktur.py:/h.py:ro \
    -w /app/backend/api_gateway --entrypoint python $IMG -I /h.py $m > $L/h-${m:-baru}.log 2>&1 < /dev/null; echo "rc=$?"
  grep -vE "^PASS |pydantic|PydanticSerial|return self.__pydantic" $L/h-${m:-baru}.log | tail -20; echo "PASS: $(grep -c "^PASS " $L/h-${m:-baru}.log)"
  [ "$POHON" != "$W" ] && rm -rf $POHON
done
rm -f $E
echo SELESAI_SEMUA
