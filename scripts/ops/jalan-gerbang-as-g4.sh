#!/usr/bin/env bash
# AS-G4 Pengiriman: tes terfokus (SEMUA pemanggil + teks-sumber + pemindai) + harness nyata kaos (rollback) baru/merah_idem/merah_penentu.
W=${W:-/root/mh-asg4}
cd $W && git fetch -q deploy && echo "HEAD $(git rev-parse --short HEAD) master $(git rev-parse --short deploy/master)" && (git merge-base --is-ancestor deploy/master HEAD && echo on-top || echo MASTER-MOVED)
L=/root/logs/asg4-$(date -u +%Y%m%dT%H%M%S); mkdir -p $L; echo "log lengkap: $L"
F=$(cd backend/api_gateway && grep -rlnE "void_sj|ubah_serah_surat_jalan|preview_ubah_serah|_rencana_serah|routers import (deliveries|sj_so)|routers\.(deliveries|sj_so)|from app.routers.(deliveries|sj_so)|idem_buat" tests/unit --include=*.py | sort -u | tr "\n" " ")
echo "=== tes terfokus ($(echo $F | wc -w) berkas)"
timeout 900 scripts/jalankan_unit.sh $W -q -p no:cacheprovider $F tests/unit/test_as_g4_pengiriman.py tests/unit/test_pagar_rute_izin.py tests/unit/test_izin_rute_pratinjau.py \
  tests/unit/test_rute_endpoint_bukan_helper.py tests/unit/test_nomor_jurnal_satu_ruang.py tests/unit/test_idem_sumber_basi.py > $L/unit.log 2>&1
grep -E "^(FAILED|ERROR) |^=+ .* in " $L/unit.log | tail -15
echo "=== kontrol merah pemindai izin pratinjau (salinan pohon: izin pratinjau serah U -> C, penjaga WAJIB merah)"
T=$(mktemp -d /root/.sabg4-XXXX); mkdir -p $T/backend
rsync -a --exclude .pytest_cache --exclude __pycache__ backend/api_gateway $T/backend/ && ln -s $W/backend/migrations $T/backend/migrations
POLA='(r"^/api/deliveries/[^/]+/serah/preview$", ["POST"], "sales_invoice", "U")'
if [ "$(grep -cF "$POLA" $T/backend/api_gateway/app/middleware/permission_middleware.py)" = 1 ]; then
  sed -i 's#serah/preview\$", \["POST"\], "sales_invoice", "U")#serah/preview$", ["POST"], "sales_invoice", "C")#' $T/backend/api_gateway/app/middleware/permission_middleware.py
  grep -c 'serah/preview$", \["POST"\], "sales_invoice", "C")' $T/backend/api_gateway/app/middleware/permission_middleware.py | sed 's/^/  sabotase terpasang: /'
  TES=tests/unit/test_izin_rute_pratinjau.py::test_tiap_pratinjau_izinnya_sama_dengan_tulisnya
  timeout 300 scripts/jalankan_unit.sh $T -q -p no:cacheprovider "$TES" > $L/sab-izin.log 2>&1; rc=$?
  if [ $rc -ne 0 ] && grep -q "^FAILED $TES" $L/sab-izin.log && grep -q "serah/preview" $L/sab-izin.log; then echo "PASS MERAH izin: pratinjau serah ber-izin C (tulis U) -> penjaga gagal"; else echo "FAIL izin: penjaga TIDAK merah (rc=$rc)"; fi
else echo "FAIL izin: pola izin pratinjau tak ditemukan untuk disabotase"; fi
rm -rf $T
G=milkyhoop-dev-api_gateway; IMG=$(docker inspect $G --format "{{.Config.Image}}"); E=$(mktemp /root/.g4-XXXX); chmod 600 $E
printf "URL=%s\n" "$(docker exec $G printenv DATABASE_URL)" > $E
for m in "" merah_idem merah_penentu; do
  echo "=== harness ${m:-baru}"
  timeout 600 docker run --rm --network milkyhoop_dev_network --env-file $E -v $W/backend:/app/backend:ro -v $W/scripts/ops/uji-nyata-as-g4.py:/h.py:ro \
    -w /app/backend/api_gateway --entrypoint python $IMG -I /h.py baru $m > $L/h-${m:-baru}.log 2>&1 < /dev/null; echo "rc=$?"
  grep -vE "^PASS |^CONTOH |pydantic|PydanticSerial|return self.__pydantic" $L/h-${m:-baru}.log | tail -25; echo "PASS: $(grep -c "^PASS " $L/h-${m:-baru}.log)"
done
rm -f $E
echo "sisa serah TES E2E: $(docker exec -i milkyhoop-dev-postgres-1 psql -U postgres -d milkydb -At -c "SELECT count(*) FROM pengiriman_serah WHERE kirim_ke LIKE 'TES E2E%'" < /dev/null)"
echo SELESAI_SEMUA
