#!/usr/bin/env bash
# AS-G10 UM Pelanggan: tes terfokus (SEMUA pemanggil + teks-sumber + pemindai) + sabotase penjaga P1..P7 (salinan pohon, WAJIB merah)
# + harness nyata kaos baru/merah_idem/merah_kunci (merah_kunci = kode NYATA dari salinan pohon yang disabotase).
W=${W:-/root/mh-g10}
cd $W && git fetch -q deploy && echo "HEAD $(git rev-parse --short HEAD) master $(git rev-parse --short deploy/master)" && (git merge-base --is-ancestor deploy/master HEAD && echo on-top || echo MASTER-MOVED)
L=/root/logs/g10-$(date -u +%Y%m%dT%H%M%S); mkdir -p $L; echo "log lengkap: $L"
F=$(cd backend/api_gateway && grep -rlnE "update_customer_deposit|delete_customer_deposit|post_customer_deposit|create_customer_deposit|_post_deposit|_ubah_dp|preview_edit_customer_deposit|routers import customer_deposits|routers\.customer_deposits|customer_deposits as|idem_buat|kunci_dari|permission_middleware|izin_untuk_jalur" tests/unit --include=*.py | sort -u | tr "\n" " ")
echo "=== tes terfokus ($(echo $F | wc -w) berkas)"
timeout 900 scripts/jalankan_unit.sh $W -q -p no:cacheprovider $F tests/unit/test_as_g10.py tests/unit/test_pemindai_idem_ast.py tests/unit/test_pagar_rute_izin.py tests/unit/test_izin_rute_pratinjau.py \
  tests/unit/test_rute_endpoint_bukan_helper.py tests/unit/test_nomor_jurnal_satu_ruang.py tests/unit/test_idem_sumber_basi.py tests/unit/test_t10b_tanggal_bisnis_arap.py > $L/unit.log 2>&1
grep -E "^(FAILED|ERROR) |^=+ .* in " $L/unit.log | tail -15
salin() {  # salinan pohon -> $POHON; sabotase $1
  POHON=$(mktemp -d /root/.sabg10-XXXX); mkdir -p $POHON/backend $POHON/scripts
  rsync -a --exclude .pytest_cache --exclude __pycache__ backend/api_gateway $POHON/backend/ && ln -s $W/backend/migrations $POHON/backend/migrations
  python3 scripts/ops/sabotase-as-g10.py $POHON/backend/api_gateway $1
}
TES_P="tests/unit/test_as_g10.py tests/unit/test_pemindai_idem_ast.py tests/unit/test_pelanggan_satu_tenant.py tests/unit/test_audit_uang_muka.py"
for s in P1 P2 P3 P4 P5 P6 P7; do
  echo "=== sabotase $s (WAJIB merah)"
  if salin $s; then
    timeout 300 scripts/jalankan_unit.sh $POHON -q -p no:cacheprovider $TES_P 2>&1 | grep -E "^(FAILED|ERROR) |^=+ .* in " | tail -6
  else echo "FAIL sabotase $s tak terpasang"; fi
  rm -rf $POHON
done
G=milkyhoop-dev-api_gateway; IMG=$(docker inspect $G --format "{{.Config.Image}}"); E=$(mktemp /root/.g10-XXXX); chmod 600 $E
printf "URL=%s\n" "$(docker exec $G printenv DATABASE_URL)" > $E
for m in "" merah_idem merah_kunci; do
  echo "=== harness ${m:-baru}"
  POHON=$W
  if [ "$m" = merah_kunci ]; then salin merah_kunci || { echo "FAIL sabotase tak terpasang"; rm -rf $POHON; continue; }; fi
  timeout 600 docker run --rm --network milkyhoop_dev_network --env-file $E -v $POHON/backend:/app/backend:ro -v $W/scripts/ops/uji-nyata-as-g10.py:/h.py:ro \
    -w /app/backend/api_gateway --entrypoint python $IMG -I /h.py baru $m > $L/h-${m:-baru}.log 2>&1 < /dev/null; echo "rc=$?"
  grep -vE "^PASS |pydantic|PydanticSerial|return self.__pydantic" $L/h-${m:-baru}.log | tail -25; echo "PASS: $(grep -c "^PASS " $L/h-${m:-baru}.log)"
  [ "$POHON" != "$W" ] && rm -rf $POHON
done
rm -f $E
echo "sisa UM TES E2E AS-G10 belum void: $(docker exec -i milkyhoop-dev-postgres-1 psql -U postgres -d milkydb -At -c "SELECT count(*) FROM customer_deposits WHERE tenant_id = 'kaos-biru-konveksi' AND reference LIKE 'TES E2E AS-G10%' AND status <> 'void'" < /dev/null)"
echo SELESAI_SEMUA
