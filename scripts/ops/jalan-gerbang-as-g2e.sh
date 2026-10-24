#!/usr/bin/env bash
# AS-G2e: tes terfokus (SEMUA pemanggil rute/penentu SO + teks-sumber + pemindai) + sabotase U1/U2 (salinan, WAJIB merah)
# + harness ROLLBACK kaos LAMA (pohon utama = kode terpasang) vs BARU (worktree) atas SEMUA SO draf kaos x 7 jenis badan -> banding_g2e
# + kontrol merah merah_riwayat / merah_pratinjau (kode NYATA dari salinan pohon) -> banding WAJIB GAGAL.
W=${W:-/root/mh-g2e}; LAMA=/root/milkyhoop-dev
cd $W && git fetch -q deploy && echo "HEAD $(git rev-parse --short HEAD) master $(git rev-parse --short deploy/master) lama=$(git -C $LAMA rev-parse --short HEAD)" && (git merge-base --is-ancestor deploy/master HEAD && echo on-top || echo MASTER-MOVED)
L=/root/logs/g2e-$(date -u +%Y%m%dT%H%M%S); mkdir -p $L/out; chmod 777 $L/out; echo "log lengkap: $L"
F=$(cd backend/api_gateway && grep -rlnE "update_sales_order|preview_edit_sales_order|_rencana_ubah_draf|_tulis_ubah_draf|_terapkan_nomor_so|sales_orders as SO|routers import sales_orders|routers\.sales_orders|SO_IS_DRAFT|so_ubah_terkonfirmasi" tests/unit --include=*.py | sort -u | tr "\n" " ")
echo "=== tes terfokus ($(echo $F | wc -w) berkas)"
timeout 900 scripts/jalankan_unit.sh $W -q -p no:cacheprovider $F tests/unit/test_as_g2e.py tests/unit/test_pemindai_idem_ast.py tests/unit/test_pagar_rute_izin.py \
  tests/unit/test_izin_rute_pratinjau.py tests/unit/test_rute_endpoint_bukan_helper.py tests/unit/test_nomor_jurnal_satu_ruang.py tests/unit/test_idem_sumber_basi.py \
  tests/unit/test_t10b_tanggal_bisnis_arap.py > $L/unit.log 2>&1
grep -E "^(FAILED|ERROR) |^=+ .* in " $L/unit.log | tail -15
salin() { POHON=$(mktemp -d /root/.sabg2e-XXXX); mkdir -p $POHON/backend
  rsync -a --exclude .pytest_cache --exclude __pycache__ backend/api_gateway $POHON/backend/ && ln -s $W/backend/migrations $POHON/backend/migrations
  python3 scripts/ops/sabotase-as-g2e.py $POHON/backend/api_gateway $1; }
for s in U1 U2; do
  echo "=== sabotase $s (WAJIB merah)"
  if salin $s; then timeout 300 scripts/jalankan_unit.sh $POHON -q -p no:cacheprovider tests/unit/test_as_g2e.py 2>&1 | grep -E "^(FAILED|ERROR) |^=+ .* in " | tail -5
  else echo "FAIL sabotase $s tak terpasang"; fi
  rm -rf $POHON
done
G=milkyhoop-dev-api_gateway; IMG=$(docker inspect $G --format "{{.Config.Image}}"); E=$(mktemp /root/.g2e-XXXX); chmod 600 $E
printf "URL=%s\n" "$(docker exec $G printenv DATABASE_URL)" > $E
jalan() {  # $1 = pohon backend, $2 = nama keluaran, $3 = mode
  timeout 900 docker run --rm --network milkyhoop_dev_network --env-file $E -v $1:/app/backend:ro -v $W/scripts/ops/uji-nyata-as-g2e.py:/h.py:ro \
    -v $L/out:/out -w /app/backend/api_gateway --entrypoint python $IMG -I /h.py /out/$2.jsonl $3 > $L/h-$2.log 2>&1 < /dev/null; echo "rc=$? $(tail -1 $L/h-$2.log)"; }
echo "=== harness LAMA (kode terpasang)"; jalan $LAMA/backend lama lama
echo "=== harness BARU"; jalan $W/backend baru baru
echo "=== banding LAMA vs BARU (WAJIB LULUS)"; python3 scripts/ops/banding-as-g2e.py $L/out/lama.jsonl $L/out/baru.jsonl | tail -14
for m in merah_riwayat merah_pratinjau; do
  echo "=== kontrol merah $m (banding WAJIB GAGAL)"
  if salin $m; then jalan $POHON/backend $m baru; python3 scripts/ops/banding-as-g2e.py $L/out/lama.jsonl $L/out/$m.jsonl | tail -4
  else echo "FAIL sabotase $m tak terpasang"; fi
  rm -rf $POHON
done
rm -f $E
echo SELESAI_SEMUA
