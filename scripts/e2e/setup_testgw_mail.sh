#!/bin/bash
# Gateway UJI :8002 — meniru mount & command gateway live, tapi menunjuk worktree.
set -u
# Jalur worktree JANGAN di-hardcode: worktree berumur pendek, skripnya tidak.
# Sama dengan kelas HARNESS-MIGDIR-VERIFIES-WRONG-TREE. Default = tree yang
# di-deploy, karena itu yang paling sering ingin diuji.
WT=${WT:-/root/milkyhoop-dev}
[ -d "$WT/backend/api_gateway/app" ] || { echo "!! WT=$WT tak memuat backend/api_gateway/app — KEGAGALAN ALAT"; exit 2; }
docker rm -f mh-testgw-mail >/dev/null 2>&1
# `docker rm -f` kembali SEBELUM bind-mount container lama benar-benar dilepas.
# Membuat container baru di celah itu menghasilkan /app/backend/api_gateway KOSONG
# -> ModuleNotFoundError, dan gate salah membacanya sebagai "gateway tak siap".
for _ in $(seq 1 30); do docker inspect mh-testgw-mail >/dev/null 2>&1 || break; sleep 1; done
sleep 1
docker inspect milkyhoop-dev-api_gateway -f '{{range .Config.Env}}{{println .}}{{end}}' | grep -v '^$' > /tmp/gw.env
# ARAH 1 menuntut kunci BENAR-BENAR kosong. Sejak kunci Resend asli terpasang di
# gateway live, docker inspect ikut membawanya ke container uji -> arah 1 mustahil
# dan gate menolak memberi verdict. Buang kunci dari env dasar; arah 2 menyetelnya
# kembali lewat -e di bawah.
grep -v '^RESEND_API_KEY=' /tmp/gw.env > /tmp/gw.env.nokey && mv /tmp/gw.env.nokey /tmp/gw.env

docker run -d --name mh-testgw-mail \
  --network milkyhoop_dev_network \
  --env-file /tmp/gw.env ${1:+-e RESEND_API_KEY=re_PALSU_hanya_untuk_uji} \
  -p 127.0.0.1:8002:8000 \
  -v "$WT/backend/api_gateway:/app/backend/api_gateway" \
  -v "$WT/backend/services/accounting_kernel:/app/backend/services/accounting_kernel" \
  -w /app \
  --entrypoint tini \
  milkyhoop-dev-api_gateway \
  -- python -B -m uvicorn backend.api_gateway.app.main:app --host 0.0.0.0 --port 8000 >/dev/null \
  || { echo "RUN GAGAL"; exit 1; }

for i in $(seq 1 40); do
  code=$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8002/healthz 2>/dev/null)
  [ "$code" = "200" ] && { echo "GATEWAY UJI SIAP setelah ${i}s"; exit 0; }
  sleep 1
done
echo "TIDAK SIAP:"; docker logs --tail 20 mh-testgw-mail
exit 1
