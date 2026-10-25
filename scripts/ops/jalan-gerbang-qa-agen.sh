#!/usr/bin/env bash
# QA-AGEN: tes terfokus (SEMUA tes yang menyentuh aksi penawaran + pemindai) + sabotase (WAJIB merah) + harness ROLLBACK kaos LAMA vs BARU + banding + kontrol merah.
W=${W:-/root/mh-qaksi}; LAMA=/root/milkyhoop-dev
cd $W && git fetch -q deploy && echo "HEAD $(git rev-parse --short HEAD) master $(git rev-parse --short deploy/master) lama=$(git -C $LAMA rev-parse --short HEAD)" && (git merge-base --is-ancestor deploy/master HEAD && echo on-top || echo MASTER-MOVED)
L=/root/logs/qaagen-$(date -u +%Y%m%dT%H%M%S); mkdir -p $L/out; chmod 777 $L/out; echo "log: $L"
F=$(cd backend/api_gateway && grep -rlnE "send_quote|accept_quote|decline_quote|delete_quote|kirim_penawaran_core|hapus_penawaran_core|rencana_kirim_penawaran|rencana_hapus_penawaran|routers import quotes|routers\.quotes|quotes as Q|permission_middleware|ROUTE_PERMISSIONS" tests/unit --include=*.py | sort -u | tr "\n" " ")
echo "=== tes terfokus ($(echo $F | wc -w) berkas)"
timeout 900 scripts/jalankan_unit.sh $W -q -p no:cacheprovider $F tests/unit/test_qa_agen.py tests/unit/test_pemindai_idem_ast.py tests/unit/test_pagar_rute_izin.py \
  tests/unit/test_izin_rute_pratinjau.py tests/unit/test_rute_endpoint_bukan_helper.py tests/unit/test_nomor_jurnal_satu_ruang.py tests/unit/test_idem_sumber_basi.py \
  tests/unit/test_t10b_tanggal_bisnis_arap.py > $L/unit.log 2>&1
grep -E "^(FAILED|ERROR) |^=+ .* in " $L/unit.log | tail -15
salin() { POHON=$(mktemp -d /root/.sabqaagen-XXXX); mkdir -p $POHON/backend
  rsync -a --exclude .pytest_cache --exclude __pycache__ backend/api_gateway $POHON/backend/ && ln -s $W/backend/migrations $POHON/backend/migrations
  python3 scripts/ops/sabotase-qa-agen.py $POHON/backend/api_gateway $1; }
for s in S_idem S_paritas S_tx; do
  echo "=== sabotase $s (WAJIB merah)"
  if salin $s; then timeout 300 scripts/jalankan_unit.sh $POHON -q -p no:cacheprovider tests/unit/test_qa_agen.py tests/unit/test_bulk_f3.py 2>&1 | grep -E "^(FAILED|ERROR) |^=+ .* in " | tail -5
  else echo "FAIL sabotase $s tak terpasang"; fi
  rm -rf $POHON
done
G=milkyhoop-dev-api_gateway; IMG=$(docker inspect $G --format "{{.Config.Image}}"); E=$(mktemp /root/.qaagen-XXXX); chmod 600 $E
printf "URL=%s\n" "$(docker exec $G printenv DATABASE_URL)" > $E
jalan() {
  timeout 900 docker run --rm --network milkyhoop_dev_network --env-file $E -v $1:/app/backend:ro -v $W/scripts/ops/uji-nyata-qa-agen.py:/h.py:ro \
    -v $L/out:/out -w /app/backend/api_gateway --entrypoint python $IMG -I /h.py /out/$2.jsonl $3 > $L/h-$2.log 2>&1 < /dev/null; echo "rc=$? $(tail -1 $L/h-$2.log)"; }
echo "=== harness LAMA"; jalan $LAMA/backend lama lama
echo "=== harness LAMA2 (bukti tak-deterministik)"; jalan $LAMA/backend lama2 lama
echo "=== LAMA vs LAMA2 (WAJIB hanya kolom waktu)"; python3 scripts/ops/nondet-qa-agen.py $L/out/lama.jsonl $L/out/lama2.jsonl $L/out/nondet.json
echo "=== harness BARU"; jalan $W/backend baru baru
echo "=== banding LAMA vs BARU (WAJIB LULUS)"; python3 scripts/ops/banding-qa-agen.py $L/out/lama.jsonl $L/out/baru.jsonl $L/out/nondet.json | tail -12
for m in M_ulang M_pratinjau; do
  echo "=== kontrol merah $m (banding WAJIB GAGAL)"
  if salin $m; then jalan $POHON/backend $m baru; python3 scripts/ops/banding-qa-agen.py $L/out/lama.jsonl $L/out/$m.jsonl $L/out/nondet.json | tail -4
  else echo "FAIL sabotase $m tak terpasang"; fi
  rm -rf $POHON
done
rm -f $E
echo SELESAI_SEMUA
