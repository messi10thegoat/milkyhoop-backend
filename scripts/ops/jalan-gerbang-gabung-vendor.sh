#!/usr/bin/env bash
# GABUNG-VENDOR: tes terfokus (semua penyentuh vendors.py/idem + SEMUA tes izin + gate_izin_rute) + sabotase WAJIB merah + harness ROLLBACK kaos
# LAMA, LAMA2 (bukti deterministik), BARU + banding + kontrol merah.
W=/root/mh-gabv; LAMA=/root/milkyhoop-dev; REPO=$LAMA
cd $W && git fetch -q deploy && echo "HEAD $(git rev-parse --short HEAD) master $(git rev-parse --short deploy/master) lama=$(git -C $LAMA rev-parse --short HEAD)" && (git merge-base --is-ancestor deploy/master HEAD && echo on-top || echo MASTER-MOVED)
L=/root/logs/gabv-$(date -u +%Y%m%dT%H%M%S); mkdir -p $L/out; chmod 777 $L/out; echo "log: $L"
echo "=== gate_izin_rute.py (skrip master, pohon worktree :ro)"
git -C $REPO show deploy/master:scripts/gate_izin_rute.py > /tmp/.izg-gv.py
docker run --rm -i --network milkyhoop_dev_network --env-file $REPO/.env -v $W/backend:/app/backend:ro -w /app milkyhoop-dev-api_gateway:latest python - < /tmp/.izg-gv.py 2>&1 | grep -E "^(HIJAU|MERAH): |\[X\]"; rm -f /tmp/.izg-gv.py
F=$(cd backend/api_gateway && { grep -rlnE "routers import vendors|routers\.vendors|vendors as V|create_vendor|toggle_vendor_status|delete_vendor|merge_vendors|SUMBER_BUAT|idem_buat|fitur_parkir" tests/unit --include=*.py; ls tests/unit/test_izin_*.py; } | sort -u | tr "\n" " ")
echo "=== tes terfokus ($(echo $F | wc -w) berkas)"
timeout 900 scripts/jalankan_unit.sh $W -q -p no:cacheprovider $F tests/unit/test_vendor_idem.py tests/unit/test_gabung_vendor.py tests/unit/test_pemindai_idem_ast.py tests/unit/test_pagar_rute_izin.py \
  tests/unit/test_rute_endpoint_bukan_helper.py tests/unit/test_nomor_jurnal_satu_ruang.py tests/unit/test_idem_sumber_basi.py tests/unit/test_t10b_tanggal_bisnis_arap.py > $L/unit.log 2>&1
grep -E "^(FAILED|ERROR) |^=+ .* in " $L/unit.log | tail -15
salin() { POHON=$(mktemp -d /root/.sabgv-XXXX); mkdir -p $POHON/backend
  rsync -a --exclude .pytest_cache --exclude __pycache__ backend/api_gateway $POHON/backend/ && ln -s $W/backend/migrations $POHON/backend/migrations
  python3 scripts/ops/sabotase-gabung-vendor.py $POHON/backend/api_gateway $1; }
for s in S_hantu S_urut S_tabel S_status; do
  echo "=== sabotase $s (WAJIB merah)"
  if salin $s; then timeout 300 scripts/jalankan_unit.sh $POHON -q -p no:cacheprovider tests/unit/test_gabung_vendor.py tests/unit/test_vendor_idem.py 2>&1 | grep -E "^(FAILED|ERROR) |^=+ .* in " | tail -5
  else echo "FAIL sabotase $s tak terpasang"; fi
  rm -rf $POHON
done
G=milkyhoop-dev-api_gateway; IMG=$(docker inspect $G --format "{{.Config.Image}}"); E=$(mktemp /root/.gv-XXXX); chmod 600 $E
printf "URL=%s\n" "$(docker exec $G printenv DATABASE_URL)" > $E
jalan() {
  timeout 900 docker run --rm --network milkyhoop_dev_network --env-file $E -v $1:/app/backend:ro -v $W/scripts/ops/uji-nyata-gabung-vendor.py:/h.py:ro \
    -v $L/out:/out -w /app/backend/api_gateway --entrypoint python $IMG -I /h.py /out/$2.jsonl $3 > $L/h-$2.log 2>&1 < /dev/null; echo "rc=$? $(tail -1 $L/h-$2.log)"; }
echo "=== harness LAMA (kode terpasang: 409 parkir)"; jalan $LAMA/backend lama lama
echo "=== harness BARU"; jalan $W/backend baru baru
echo "=== banding (WAJIB LULUS)"; python3 scripts/ops/banding-gabung-vendor.py $L/out/baru.jsonl $L/out/lama.jsonl | tail -14
for m in M_ap M_pratinjau M_status; do
  echo "=== kontrol merah $m (banding WAJIB GAGAL)"
  if salin $m; then jalan $POHON/backend $m baru; python3 scripts/ops/banding-gabung-vendor.py $L/out/$m.jsonl | tail -4; else echo FAIL; fi; rm -rf $POHON
done
rm -f $E
echo SELESAI_SEMUA
