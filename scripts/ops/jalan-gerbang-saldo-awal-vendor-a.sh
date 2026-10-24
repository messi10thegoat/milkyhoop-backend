#!/usr/bin/env bash
# SALDO-AWAL-VENDOR-A: tes terfokus (pemanggil post_bill / tagihan_aksi / set_vendor_opening_balance + teks-sumber + pemindai) + sabotase U1..U3
# (salinan pohon, WAJIB merah) + harness ROLLBACK kaos baru/merah_debit (V458 DALAM rollback bila belum diterapkan).
W=${W:-/root/mh-voba}
cd $W && git fetch -q deploy && echo "HEAD $(git rev-parse --short HEAD) master $(git rev-parse --short deploy/master)" && (git merge-base --is-ancestor deploy/master HEAD && echo on-top || echo MASTER-MOVED)
L=/root/logs/voba-$(date -u +%Y%m%dT%H%M%S); mkdir -p $L; echo "log lengkap: $L"
F=$(cd backend/api_gateway && grep -rlnE "post_bill|tagihan_aksi|hitung_debit|blok_posting|memo_debit|rencana_posting|set_vendor_opening_balance|vendors as RV|routers import vendors|routers\.vendors|bills_service|VENDOR_OPENING" tests/unit --include=*.py | sort -u | tr "\n" " ")
echo "=== tes terfokus ($(echo $F | wc -w) berkas)"
timeout 900 scripts/jalankan_unit.sh $W -q -p no:cacheprovider $F tests/unit/test_saldo_awal_vendor_a.py tests/unit/test_pemindai_idem_ast.py tests/unit/test_pagar_rute_izin.py \
  tests/unit/test_izin_rute_pratinjau.py tests/unit/test_rute_endpoint_bukan_helper.py tests/unit/test_nomor_jurnal_satu_ruang.py tests/unit/test_idem_sumber_basi.py \
  tests/unit/test_t10b_tanggal_bisnis_arap.py > $L/unit.log 2>&1
grep -E "^(FAILED|ERROR) |^=+ .* in " $L/unit.log | tail -15
salin() { POHON=$(mktemp -d /root/.sabvoba-XXXX); mkdir -p $POHON/backend
  rsync -a --exclude .pytest_cache --exclude __pycache__ backend/api_gateway backend/migrations $POHON/backend/
  python3 scripts/ops/sabotase-saldo-awal-vendor-a.py $POHON/backend/api_gateway $1; }
for s in U1 U2 U3; do
  echo "=== sabotase $s (WAJIB merah)"
  if salin $s; then timeout 300 scripts/jalankan_unit.sh $POHON -q -p no:cacheprovider tests/unit/test_saldo_awal_vendor_a.py 2>&1 | grep -E "^(FAILED|ERROR) |^=+ .* in " | tail -6
  else echo "FAIL sabotase $s tak terpasang"; fi
  rm -rf $POHON
done
G=milkyhoop-dev-api_gateway; IMG=$(docker inspect $G --format "{{.Config.Image}}"); E=$(mktemp /root/.voba-XXXX); chmod 600 $E
printf "URL=%s\n" "$(docker exec $G printenv DATABASE_URL)" > $E
for m in "" merah_debit; do
  echo "=== harness ${m:-baru} (ROLLBACK kaos)"; POHON=$W
  if [ -n "$m" ]; then salin $m || { echo "FAIL sabotase tak terpasang"; rm -rf $POHON; continue; }; fi
  timeout 300 docker run --rm --network milkyhoop_dev_network --env-file $E -v $POHON/backend:/app/backend:ro -v $W/scripts/ops/uji-nyata-saldo-awal-vendor-a.py:/h.py:ro \
    -w /app/backend/api_gateway --entrypoint python $IMG -I /h.py $m > $L/h-${m:-baru}.log 2>&1 < /dev/null; echo "rc=$?"
  grep -vE "^PASS |pydantic|PydanticSerial|return self.__pydantic" $L/h-${m:-baru}.log | tail -15; echo "PASS: $(grep -c "^PASS " $L/h-${m:-baru}.log)"
  [ "$POHON" != "$W" ] && rm -rf $POHON
done
rm -f $E
echo SELESAI_SEMUA
