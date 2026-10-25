#!/usr/bin/env bash
# QA-AGEN: SEMUA cek izin rute atas pohon worktree (cara SAMA dengan mh-gerbang-merge 3b) + sabotase izin delete/preview=C WAJIB merah.
W=/root/mh-qaksi; REPO=/root/milkyhoop-dev; cd $W
echo "HEAD $(git rev-parse --short HEAD) master $(git rev-parse --short deploy/master)"
echo "=== gate_izin_rute.py (skrip dari master, pohon worktree :ro)"
git -C $REPO show deploy/master:scripts/gate_izin_rute.py > /tmp/.izg-qa.py
docker run --rm -i --network milkyhoop_dev_network --env-file $REPO/.env -v $W/backend:/app/backend:ro -w /app milkyhoop-dev-api_gateway:latest python - < /tmp/.izg-qa.py 2>&1 | grep -E "^(HIJAU|MERAH): |\[X\]"; rm -f /tmp/.izg-qa.py
echo "=== tes izin (semua test_izin_* + pagar)"
timeout 300 scripts/jalankan_unit.sh $W -q -p no:cacheprovider $(cd backend/api_gateway && ls tests/unit/test_izin_*.py tests/unit/test_pagar_rute_izin.py tests/unit/test_bulk_f3.py tests/unit/test_qa_agen.py) 2>&1 | grep -E "^(FAILED|ERROR) |^=+ .* in " | tail -8
echo "=== sabotase izin delete/preview = C (test_izin_rute_pratinjau WAJIB merah)"
P=$(mktemp -d /root/.sabiz-XXXX); mkdir -p $P/backend; rsync -a --exclude __pycache__ backend/api_gateway $P/backend/ && ln -s $W/backend/migrations $P/backend/migrations
python3 scripts/ops/sabotase-izin-qa-agen.py $P/backend/api_gateway && timeout 300 scripts/jalankan_unit.sh $P -q -p no:cacheprovider tests/unit/test_izin_rute_pratinjau.py 2>&1 | grep -E "^(FAILED|ERROR) |^=+ .* in " | tail -4; rm -rf $P
echo SELESAI
