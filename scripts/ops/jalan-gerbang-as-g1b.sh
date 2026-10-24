#!/usr/bin/env bash
# AS-G1b: tes terfokus (pemanggil update_vendor_deposit + teks-sumber + pemindai) + sabotase salinan (If-Match kembali sebelum replay, WAJIB merah)
# + pindai READ-ONLY handler lain yang memeriksa If-Match SEBELUM idem (laporan, bukan gerbang).
W=${W:-/root/mh-g1b}
cd $W && git fetch -q deploy && echo "HEAD $(git rev-parse --short HEAD) master $(git rev-parse --short deploy/master)" && (git merge-base --is-ancestor deploy/master HEAD && echo on-top || echo MASTER-MOVED)
L=/root/logs/g1b-$(date -u +%Y%m%dT%H%M%S); mkdir -p $L; echo "log lengkap: $L"
F=$(cd backend/api_gateway && grep -rlnE "update_quote|_ubah_penawaran|quotes as RQ|routers import quotes|routers\.quotes|assert_if_match_row|optimistic_concurrency" tests/unit --include=*.py | sort -u | tr "\n" " ")
echo "=== tes terfokus ($(echo $F | wc -w) berkas)"
timeout 600 scripts/jalankan_unit.sh $W -q -p no:cacheprovider $F tests/unit/test_as_g1b.py tests/unit/test_milkybook_t0.py tests/unit/test_pemindai_idem_ast.py \
  tests/unit/test_pagar_rute_izin.py tests/unit/test_izin_rute_pratinjau.py tests/unit/test_rute_endpoint_bukan_helper.py > $L/unit.log 2>&1
grep -E "^(FAILED|ERROR) |^=+ .* in " $L/unit.log | tail -10
echo "=== sabotase If-Match sebelum replay (WAJIB merah)"
P=$(mktemp -d /root/.sabg1b-XXXX); mkdir -p $P/backend
rsync -a --exclude .pytest_cache --exclude __pycache__ backend/api_gateway $P/backend/ && ln -s $W/backend/migrations $P/backend/migrations
if python3 scripts/ops/sabotase-as-g1b.py $P/backend/api_gateway; then
  timeout 300 scripts/jalankan_unit.sh $P -q -p no:cacheprovider tests/unit/test_as_g1b.py 2>&1 | grep -E "^(FAILED|ERROR) |^=+ .* in " | tail -5
else echo "FAIL sabotase tak terpasang"; fi
rm -rf $P
G=milkyhoop-dev-api_gateway; IMG=$(docker inspect $G --format "{{.Config.Image}}"); E=$(mktemp /root/.g1b-XXXX); chmod 600 $E
printf "URL=%s\n" "$(docker exec $G printenv DATABASE_URL)" > $E
for m in "" merah_ifmatch; do
  echo "=== harness ${m:-baru} (ROLLBACK kaos)"; POHON=$W
  if [ -n "$m" ]; then POHON=$(mktemp -d /root/.sabg1b-XXXX); mkdir -p $POHON/backend
    rsync -a --exclude .pytest_cache --exclude __pycache__ backend/api_gateway $POHON/backend/ && ln -s $W/backend/migrations $POHON/backend/migrations
    python3 scripts/ops/sabotase-as-g1b.py $POHON/backend/api_gateway || { echo "FAIL sabotase tak terpasang"; rm -rf $POHON; continue; }; fi
  timeout 300 docker run --rm --network milkyhoop_dev_network --env-file $E -v $POHON/backend:/app/backend:ro -v $W/scripts/ops/uji-nyata-as-g1b.py:/h.py:ro \
    -w /app/backend/api_gateway --entrypoint python $IMG -I /h.py $m > $L/h-${m:-baru}.log 2>&1 < /dev/null; echo "rc=$?"
  grep -vE "^PASS |pydantic|PydanticSerial|return self.__pydantic" $L/h-${m:-baru}.log | tail -12; echo "PASS: $(grep -c "^PASS " $L/h-${m:-baru}.log)"
  [ "$POHON" != "$W" ] && rm -rf $POHON
done
rm -f $E
echo "=== pindai: handler ber-If-Match + idem (posisi If-Match vs idem) -- laporan"
python3 - <<'PY'
import ast, pathlib
for f in sorted(pathlib.Path("backend/api_gateway/app/routers").glob("*.py")):
    s = f.read_text()
    for n in ast.walk(ast.parse(s)):
        if isinstance(n, ast.AsyncFunctionDef):
            seg = ast.get_source_segment(s, n)
            if "assert_if_match_row(" in seg and ("mulai_aksi(" in seg or "mulai(" in seg or "execute_idempotent" in seg):
                i = seg.index("await assert_if_match_row(")
                j = min(x for x in (seg.find("mulai_aksi("), seg.find("mulai("), seg.find("execute_idempotent")) if x >= 0)
                print(f"{'SEBELUM-idem' if i < j else 'sesudah-idem'}  {f.name}:{n.name}")
PY
echo SELESAI_SEMUA
