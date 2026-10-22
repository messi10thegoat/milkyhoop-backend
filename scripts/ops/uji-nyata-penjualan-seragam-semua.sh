#!/usr/bin/env bash
# PENJAGA GABUNGAN Penjualan seragam (21 Okt 2026, MASTER): menjalankan SEMUA harness seragam BACA-SAJA terhadap kode LIVE (pohon utama),
# keluaran satu tabel lulus/gagal per modul, rc != 0 bila ada yang gagal. Pakai: sesudah TIAP rilis BE dan SEBELUM FE merilis modul.
#
#   bash scripts/ops/uji-nyata-penjualan-seragam-semua.sh                 # semua, kode live (MH_MAIN=/root/milkyhoop-dev)
#   bash scripts/ops/uji-nyata-penjualan-seragam-semua.sh --kontrol-merah # buktikan penjaga BISA merah: harness penerimaan dijalankan
#                                                                          # terhadap SALINAN pohon yang disabotase -> harus GAGAL (rc 0 = terdeteksi)
#
# Semua kueri DB read-only (default_transaction_read_only / ROLLBACK), kontainer sekali-pakai, DSN lewat berkas 600 sementara (tak dicetak).
# Mode "kode live" saja (tanpa lama vs baru): tiap harness memeriksa KONSISTENSI terhadap sumber independen (SQL terpisah / bundel / daftar).
# Tak ada harness untuk modul (mis. NK belum live) -> SKIP, bukan gagal. Tambah modul: satu baris di DAFTAR di bawah.
set -uo pipefail
AKAR="${MH_MAIN:-/root/milkyhoop-dev}"; G=milkyhoop-dev-api_gateway
OPS="$AKAR/scripts/ops"
IMG=$(docker inspect "$G" --format '{{.Config.Image}}')
E=$(mktemp /root/.pjseragam-env-XXXX); chmod 600 "$E"; O=$(mktemp -d /root/.pjseragam-out-XXXX); LOG=$(mktemp -d /root/.pjseragam-log-XXXX)
trap 'rm -rf "$E" "$O" "$LOG" "${SAB:-/nonexistent}"' EXIT
DSN=$(docker exec "$G" printenv DATABASE_URL); printf 'DATABASE_URL=%s\nURL=%s\n' "$DSN" "$DSN" > "$E"; unset DSN

MODE_MERAH=0; [ "${1:-}" = "--kontrol-merah" ] && MODE_MERAH=1

# jalan_py <pohon> <berkas.py> <argumen...> : kontainer sekali-pakai; pohon dipasang sebagai /app/backend (dua gaya impor didukung)
jalan_py() {
  local pohon=$1 py=$2; shift 2
  docker run --rm --network milkyhoop_dev_network --env-file "$E" -v "$pohon/backend:/app/backend:ro" -v "$O:/out" \
    -v "$py:/h.py:ro" -w /app/backend/api_gateway --entrypoint python "$IMG" -I /h.py "$@" 2>&1
}

# ---- kontrol merah ----------------------------------------------------------------------------------------------------------------
if [ "$MODE_MERAH" = 1 ]; then
  SAB=$(mktemp -d /root/.pjseragam-sab-XXXX)
  cp -a "$AKAR/backend" "$SAB/backend"
  f="$SAB/backend/api_gateway/app/services/penerimaan_daftar.py"
  python3 - "$f" <<'PY'
import sys
p = sys.argv[1]; s = open(p).read()
a = '"aksi": "belum_dialokasikan" if belum > TOLERANSI else None'
assert a in s, "pola sabotase tak ditemukan"
open(p, "w").write(s.replace(a, '"aksi": None'))
PY
  jalan_py "$SAB" "$OPS/uji-nyata-penerimaan-seragam.py" baru /out/merah.json > "$LOG/merah.out"; rc=$?
  echo
  if [ "$rc" -ne 0 ] || grep -qE "gagal=[1-9]" "$LOG/merah.out"; then
    echo "KONTROL MERAH: TERDETEKSI — penjaga merah saat harness penerimaan dijalankan terhadap pohon yang disabotase ($(grep -E 'RINGKAS' "$LOG/merah.out" | tail -1 | cut -c1-90))"
    exit 0
  fi
  echo "KONTROL MERAH: TIDAK TERDETEKSI — sabotase lolos! penjaga TIDAK BISA dipercaya"; exit 1
fi


HASIL=()   # "nama|STATUS|ringkas"
GAGAL=0
catat() { HASIL+=("$1|$2|$3"); [ "$2" = "GAGAL" ] && GAGAL=$((GAGAL+1)); return 0; }

# nilai(keluaran, rc): LULUS bila rc=0, tak ada FAIL/Traceback, ada "gagal=0" dan tak ada "gagal=[1-9]"
nilai() {
  local nama=$1 rc=$2 f=$3
  local ring; ring=$(grep -E "RINGKAS" "$f" | tail -2 | tr '\n' ' ' | cut -c1-110)
  if [ "$rc" -ne 0 ] || grep -qE "^FAIL|Traceback|Error:" "$f" || grep -qE "gagal=[1-9]" "$f" || ! grep -qE "gagal=0" "$f"; then
    catat "$nama" GAGAL "${ring:-rc=$rc}"; cp "$f" "$LOG/$nama.gagal" 2>/dev/null; return 0
  fi
  catat "$nama" LULUS "$ring"
}

# ---- DAFTAR: harness BARU milik BACKEND2 (mode `baru` = konsistensi kode live) -------------------------------------------------------
for m in penawaran penerimaan pengiriman proforma; do
  py="$OPS/uji-nyata-$m-seragam.py"
  if [ ! -f "$py" ]; then catat "$m" SKIP "harness belum ada"; continue; fi
  jalan_py "$AKAR" "$py" baru /out/$m.json > "$LOG/$m.out"; nilai "$m" $? "$LOG/$m.out"
done
py="$OPS/uji-nyata-paginasi-urut.py"
if [ -f "$py" ]; then jalan_py "$AKAR" "$py" > "$LOG/paginasi.out"; nilai "paginasi+urutan(4 modul)" $? "$LOG/paginasi.out"; else catat "paginasi+urutan" SKIP "harness belum ada"; fi
py="$OPS/uji-nyata-idempotensi-sumber.py"
if [ -f "$py" ]; then jalan_py "$AKAR" "$py" baru /out/idem.json > "$LOG/idem.out"; nilai "idempotensi-sumber" $? "$LOG/idem.out"; else catat "idempotensi-sumber" SKIP "harness belum ada"; fi
for par in "perlu-difaktur:uji-nyata-pengiriman-perlu-difaktur.py" "nk-total-applied:uji-nyata-nk-total-applied.py"; do
  nama=${par%%:*}; py="$OPS/${par#*:}"
  if [ -f "$py" ]; then jalan_py "$AKAR" "$py" baru > "$LOG/$nama.out"; nilai "$nama" $? "$LOG/$nama.out"; else catat "$nama" SKIP "harness belum ada"; fi
done
py="$OPS/uji-nyata-pill-partisi.py"
if [ -f "$py" ]; then jalan_py "$AKAR" "$py" baru > "$LOG/pill.out"; nilai "pill-partisi(8 modul)" $? "$LOG/pill.out"; else catat "pill-partisi" SKIP "harness belum ada"; fi
py="$OPS/uji-nyata-penawaran-void.py"
if [ -f "$py" ]; then jalan_py "$AKAR" "$py" baru > "$LOG/penawaran-void.out"; nilai "penawaran-void" $? "$LOG/penawaran-void.out"; else catat "penawaran-void" SKIP "harness belum ada"; fi
py="$OPS/uji-nyata-pembelian-p0.py"
if [ -f "$py" ]; then jalan_py "$AKAR" "$py" baru > "$LOG/pembelian-p0.out"; nilai "pembelian-p0" $? "$LOG/pembelian-p0.out"; else catat "pembelian-p0" SKIP "harness belum ada"; fi
py="$OPS/uji-nyata-po-seragam.py"
if [ -f "$py" ]; then jalan_py "$AKAR" "$py" baru > "$LOG/po-seragam.out"; nilai "po-seragam" $? "$LOG/po-seragam.out"; else catat "po-seragam" SKIP "harness belum ada"; fi
py="$OPS/uji-nyata-po-tulis.py"
if [ -f "$py" ]; then jalan_py "$AKAR" "$py" baru > "$LOG/po-tulis.out"; nilai "po-tulis" $? "$LOG/po-tulis.out"; else catat "po-tulis" SKIP "harness belum ada"; fi
py="$OPS/uji-nyata-um-vendor-seragam.py"
if [ -f "$py" ]; then jalan_py "$AKAR" "$py" baru > "$LOG/um-vendor.out"; nilai "um-vendor-seragam" $? "$LOG/um-vendor.out"; else catat "um-vendor-seragam" SKIP "harness belum ada"; fi
py="$OPS/uji-nyata-recurring-generate.py"
if [ -f "$py" ]; then jalan_py "$AKAR" "$py" baru > "$LOG/recurring.out"; nilai "recurring-generate" $? "$LOG/recurring.out"; else catat "recurring-generate" SKIP "harness belum ada"; fi
py="$OPS/uji-nyata-paritas-detail.py"
if [ -f "$py" ]; then jalan_py "$AKAR" "$py" baru /out/par.json > "$LOG/paritas.out"; nilai "paritas-detail(4 modul)" $? "$LOG/paritas.out"; else catat "paritas-detail" SKIP "harness belum ada"; fi

# ---- DAFTAR: harness BACKEND (jalankan pelari .sh miliknya terhadap pohon utama) ----------------------------------------------------
for par in "dokumen-faktur:uji-nyata-dokumen-faktur.py" "detail-faktur:uji-nyata-detail-faktur.py" "uang-muka:uji-nyata-um-seragam.py" "nota-kredit:uji-nyata-nk-seragam.py"; do
  nama=${par%%:*}; berkas=${par#*:}; py="$OPS/$berkas"
  if [ ! -f "$py" ]; then catat "$nama" SKIP "harness belum ada (modul belum live)"; continue; fi
  if [ "$nama" = "dokumen-faktur" ]; then
    # bundel FAKTUR ⊆ bundel SO (konsistensi), lalu bundel SO LIVE: dibangun untuk semua SO, tiap dokumen punya kind/id/sent/batal/status
    jalan_py "$AKAR" "$py" faktur > "$LOG/$nama.out"; rc=$?
    jalan_py "$AKAR" "$py" so /out/so.json > "$LOG/$nama.so.out"; rc2=$?
    python3 - "$O/so.json" >> "$LOG/$nama.out" <<'PY'
import json, sys
try:
    d = json.load(open(sys.argv[1]))
except Exception as e:
    print("FAIL bundel SO tak terbaca:", e); sys.exit(0)
n = len(d); bad = 0
for k, b in d.items():
    for g in (b.get("groups") or []):
        for x in g.get("docs", []):
            if not {"kind", "id", "sent", "batal", "status"} <= set(x): bad += 1
print(("PASS" if n >= 100 and bad == 0 else "FAIL") + f" bundel SO live: {n} bundel, dok tak lengkap={bad}")
PY
    [ "$rc2" -ne 0 ] && rc=$rc2
    nilai "$nama(SO+faktur)" $rc "$LOG/$nama.out"
  else
    jalan_py "$AKAR" "$py" > "$LOG/$nama.out"; nilai "$nama" $? "$LOG/$nama.out"
  fi
done

# ---- DAFTAR: harness BACKEND kode live tanpa lama/baru (21 Okt): paritas UM/NK/Faktur + rincian jurnal UM/NK + position_text Proforma/Pengiriman
# (uji-nyata-nk-potongan SENGAJA tak di sini: ia MENULIS kaos di transaksi ROLLBACK -- penjaga ini baca-saja.)
for par in "paritas-um-nk-faktur:uji-nyata-paritas-s2.py:paritas" "rincian-um-nk:uji-nyata-rincian-um-nk.py:" "posisi-proforma-pengiriman:uji-nyata-posisi-pro-kirim.py:"; do
  nama=${par%%:*}; sisa=${par#*:}; berkas=${sisa%%:*}; arg=${sisa#*:}; py="$OPS/$berkas"
  if [ ! -f "$py" ]; then catat "$nama" SKIP "harness belum ada"; continue; fi
  jalan_py "$AKAR" "$py" $arg > "$LOG/$nama.out"; nilai "$nama" $? "$LOG/$nama.out"
done

# ---- tinjauan gabungan DRAF_SEBAGAI_DIRINYA (rampungkan): invoice+proforma wajib ada -----------------------------------------------
cat > "$O/draf.py" <<'PY'
import os, sys
sys.path.insert(0, "/app/backend/api_gateway"); os.environ.setdefault("OPENAI_API_KEY", "x")
import inspect
from app.services import dokumen_bundel as DB
src = inspect.getsource(DB.rampungkan)
ada = getattr(DB, "DRAF_SEBAGAI_DIRINYA", None)
if ada is not None:
    ok = {"invoice", "proforma"} <= set(ada)
    print(("PASS" if ok else "FAIL"), "DRAF_SEBAGAI_DIRINYA =", sorted(ada))
else:
    ok = 'd["kind"] in ("invoice", "proforma"' in src
    print(("PASS" if ok else "FAIL"), "draf-sebagai-dirinya (literal di rampungkan)")
print("RINGKAS gagal=%d" % (0 if ok else 1))
PY
jalan_py "$AKAR" "$O/draf.py" > "$LOG/draf.out"; nilai "draf-sebagai-dirinya" $? "$LOG/draf.out"

# ---- tabel ----------------------------------------------------------------------------------------------------------------------
echo
printf '%-26s %-7s %s\n' "MODUL / HARNESS" "STATUS" "RINGKAS"
printf '%-26s %-7s %s\n' "--------------------------" "-------" "-----------------------------------------------"
for h in "${HASIL[@]}"; do IFS='|' read -r n s r <<< "$h"; printf '%-26s %-7s %s\n' "$n" "$s" "$r"; done
echo
if [ "$GAGAL" -gt 0 ]; then
  echo "PENJAGA PENJUALAN-SERAGAM: GAGAL ($GAGAL harness). Keluaran lengkap yang gagal:"
  for f in "$LOG"/*.gagal; do [ -f "$f" ] && { echo "--- $(basename "$f" .gagal)"; grep -E "FAIL|gagal=|Traceback|Error" "$f" | head -8 | cut -c1-240; }; done
  exit 1
fi
echo "PENJAGA PENJUALAN-SERAGAM: SEMUA LULUS (${#HASIL[@]} baris; SKIP = harness belum ada)"
exit 0
