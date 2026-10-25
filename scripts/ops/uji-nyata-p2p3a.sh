#!/usr/bin/env bash
# P2P-3a: ND + NK ubah/hapus idem + balapan hapus||posting. kaos. TULIS = ROLLBACK; KONK = COMMIT 'TES E2E' lalu void lewat aplikasi.
# MERAH=ii  : salinan BARU, idem hapus dipindah SESUDAH cek status -> (D) replay 404 WAJIB merah (KONK dilewati).
# MERAH=ib  : salinan BARU, hapus tanpa kunci advisory + FOR UPDATE + syarat status -> (K2) WAJIB merah.
# MERAH=ia  : salinan BARU, hanya tanpa syarat status -> (K2) tetap HIJAU (kunci saja cukup; syarat = lapis kedua).
set -uo pipefail
AKAR="$(cd "$(dirname "$0")/../.." && pwd)"; UTAMA=/root/milkyhoop-dev; G=milkyhoop-dev-api_gateway
E=$(mktemp /root/.p3a-env-XXXX); chmod 600 "$E"; O=$(mktemp -d /root/.p3a-out-XXXX); S=$(mktemp -d /root/.p3a-sab-XXXX)
trap 'mkdir -p /root/logs && cp -a "$O" "/root/logs/p3a-out-$(date -u +%Y%m%dT%H%M%S)" 2>/dev/null; rm -rf "$E" "$O" "$S"' EXIT
docker exec "$G" printenv DATABASE_URL | sed 's/^/DATABASE_URL=/' > "$E"
IMG=$(docker inspect "$G" --format '{{.Config.Image}}')
jalan() { docker run --rm --network milkyhoop_dev_network --env-file "$E" -e MODE="$2" -e NAMA="$3" -e PEMBANDING_K="${PEMBANDING_K:-baru}" \
    -v "$1/backend:/app/backend:ro" -v "$O:/out" -v "$AKAR/scripts/ops/uji-nyata-p2p3a.py:/h.py:ro" \
    -w /app/backend/api_gateway --entrypoint python "$IMG" -I /h.py 2>&1 | grep -v -E "^(INFO|DEBUG|WARNING)"; return "${PIPESTATUS[0]}"; }
sabot() {  # $1 = mode sabotase; menyalin pohon BARU ke $S lalu mengubah kedua handler hapus
  cp -a "$AKAR/backend" "$S/backend"
  python3 - "$S/backend/api_gateway/app/routers" "$1" <<'PYEOF'
import sys, re
d, m = sys.argv[1], sys.argv[2]
for f, P, K in (("vendor_credits.py", "VC_DELETE", "VENDOR_CREDIT"), ("credit_notes.py", "CN_DELETE", "CREDIT_NOTE")):
    p = f"{d}/{f}"; s = open(p).read()
    a = s.index(f"async def delete_{'vendor_credit' if f.startswith('v') else 'credit_note'}("); b = s.index("\n@router", a)
    h = s[a:b]
    if m in ("ia", "ib"):
        n = h.count(" AND status = 'draft' RETURNING id"); assert n == 1, (f, m, n)
        h = h.replace(" AND status = 'draft' RETURNING id", " RETURNING id")
    if m == "ib":
        lk = [l for l in h.splitlines() if f'f"{K}:' in l and "pg_advisory_xact_lock" in l]; assert len(lk) == 1, (f, lk)
        h = h.replace(lk[0] + "\n", "")
        assert h.count(" FOR UPDATE\"") == 1, (f, "FU"); h = h.replace(" FOR UPDATE\"", "\"")
    if m == "ii":
        idem = [l for l in h.splitlines() if f'"{P}"' in l and "mulai_aksi" in l]; assert len(idem) == 1, (f, idem)
        blok = idem[0] + "\n" + "                if _lama is not None:\n                    return _lama\n"
        assert h.count(blok) == 1, (f, "blok idem")
        h = h.replace(blok, "")
        tanda = "                # Delete (cascade will delete items)"
        assert h.count(tanda) == 1, (f, "tanda")
        h = h.replace(tanda, blok + tanda)
    s = s[:a] + h + s[b:]; open(p, "w").write(s); print(f"SABOTASE {m} {f} ok")
PYEOF
}
rc=0
case "${MERAH:-0}" in
  ii) sabot ii || exit 2
      jalan "$UTAMA" TULIS lama || rc=1
      jalan "$S" TULIS baru || rc=1
      jalan "$AKAR" BANDING x; r=$?; echo "rc_banding_merah=$r (harus 1)"; [ $r = 1 ] || rc=1; echo "rc=$rc"; exit $rc ;;
  ia|ib) sabot "$MERAH" || exit 2
      jalan "$UTAMA" TULIS lama || rc=1
      jalan "$AKAR" TULIS baru || rc=1
      jalan "$S" KONK merah || rc=1
      PEMBANDING_K=merah jalan "$AKAR" BANDING x; r=$?
      if [ "$MERAH" = ib ]; then echo "rc_banding_merah=$r (harus 1)"; [ $r = 1 ] || rc=1; else echo "rc_banding_ia=$r (harus 0: kunci saja cukup)"; [ $r = 0 ] || rc=1; fi
      echo "rc=$rc"; exit $rc ;;
esac
jalan "$UTAMA" TULIS lama || rc=1
jalan "$AKAR" TULIS baru || rc=1
jalan "$AKAR" KONK baru || rc=1
jalan "$AKAR" BANDING x || rc=1
echo "rc=$rc"; exit $rc
