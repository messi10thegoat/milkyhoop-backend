#!/usr/bin/env bash
# PO-IM: tes terfokus (SEMUA tes yang menyentuh PO PATCH + pemindai) + sabotase S1/S2 (WAJIB merah) + harness ROLLBACK kaos LAMA vs BARU + kontrol merah.
W=${W:-/root/mh-poim}; LAMA=/root/milkyhoop-dev
cd $W && git fetch -q deploy && echo "HEAD $(git rev-parse --short HEAD) master $(git rev-parse --short deploy/master) lama=$(git -C $LAMA rev-parse --short HEAD)" && (git merge-base --is-ancestor deploy/master HEAD && echo on-top || echo MASTER-MOVED)
L=/root/logs/poim-$(date -u +%Y%m%dT%H%M%S); mkdir -p $L/out; chmod 777 $L/out; echo "log: $L"
F=$(cd backend/api_gateway && grep -rlnE "update_purchase_order|PO_UPDATE|routers import purchase_orders|routers\.purchase_orders|purchase_orders as R|assert_if_match_row|optimistic_concurrency" tests/unit --include=*.py | sort -u | tr "\n" " ")
echo "=== tes terfokus ($(echo $F | wc -w) berkas)"
timeout 900 scripts/jalankan_unit.sh $W -q -p no:cacheprovider $F tests/unit/test_po_ifmatch.py tests/unit/test_pemindai_idem_ast.py tests/unit/test_pagar_rute_izin.py \
  tests/unit/test_izin_rute_pratinjau.py tests/unit/test_rute_endpoint_bukan_helper.py tests/unit/test_nomor_jurnal_satu_ruang.py tests/unit/test_idem_sumber_basi.py \
  tests/unit/test_t10b_tanggal_bisnis_arap.py > $L/unit.log 2>&1
grep -E "^(FAILED|ERROR) |^=+ .* in " $L/unit.log | tail -15
salin() { POHON=$(mktemp -d /root/.sabpoim-XXXX); mkdir -p $POHON/backend
  rsync -a --exclude .pytest_cache --exclude __pycache__ backend/api_gateway $POHON/backend/ && ln -s $W/backend/migrations $POHON/backend/migrations
  python3 scripts/ops/sabotase-po-im.py $POHON/backend/api_gateway $1; }
for s in S1 S2; do
  echo "=== sabotase $s (WAJIB merah)"
  if salin $s; then timeout 300 scripts/jalankan_unit.sh $POHON -q -p no:cacheprovider tests/unit/test_po_ifmatch.py 2>&1 | grep -E "^(FAILED|ERROR) |^=+ .* in " | tail -5
  else echo "FAIL sabotase $s tak terpasang"; fi
  rm -rf $POHON
done
G=milkyhoop-dev-api_gateway; IMG=$(docker inspect $G --format "{{.Config.Image}}"); E=$(mktemp /root/.poim-XXXX); chmod 600 $E
printf "URL=%s\n" "$(docker exec $G printenv DATABASE_URL)" > $E
jalan() {
  timeout 300 docker run --rm --network milkyhoop_dev_network --env-file $E -v $1:/app/backend:ro -v $W/scripts/ops/uji-nyata-po-im.py:/h.py:ro \
    -v $L/out:/out -w /app/backend/api_gateway --entrypoint python $IMG -I /h.py /out/$2.json $3 > $L/h-$2.log 2>&1 < /dev/null; echo "rc=$? $(tail -1 $L/h-$2.log)"; }
echo "=== harness LAMA"; jalan $LAMA/backend lama lama
echo "=== harness BARU"; jalan $W/backend baru baru
echo "=== kontrol merah M_kosong (pohon baru tanpa If-Match: B_basi WAJIB ok = sama dgn LAMA)"
if salin M_kosong; then jalan $POHON/backend merah baru; else echo "FAIL"; fi; rm -rf $POHON
rm -f $E
echo SELESAI_SEMUA
