#!/usr/bin/env bash
# GERBANG BACKEND per unit -- ALAT PENILAI (putusan MASTER 10 Nov: masuk repo, symlink /root/gerbang-unit.sh). Jalankan via nohup DI SERVER,
# tunggu baris akhir "LULUS rc=0" / "TOLAK rc=1". Pakai: gerbang-unit.sh <konfig>   (konfig contoh: scripts/ops/gerbang-konfig/*.sh)
# konfig: W (worktree), CABANG, NAMA_JENDELA, POLA_UNIT (pola grep berkas tes unit), HARNESS (uji salinan; rc 0 = lulus),
#         PENANDA_BERKAS + PENANDA (CEK_SAJA window_item), SAB=( "nama|berkas|sed-ekspresi(# pemisah)|tes" ... ).
# Vonis: TOLAK bila unit/suite ada FAILED/ERROR atau tanpa ringkasan, harness rc != 0, sabotase TETAP HIJAU / tak mengubah berkas /
#        tak terkompilasi, gate_izin_rute bukan HIJAU, penanda tak OK, atau worktree kotor. Kontrol merah: GERBANG_KONTROL_MERAH=1 merusak
#        satu berkas unit (sabotase pertama) SEBELUM langkah 1, berhenti sesudah langkah 1, dan WAJIB berakhir TOLAK rc=1.
set -u
TOLAK=()
tolak() { TOLAK+=("$1"); echo "  -> TOLAK: $1"; }
. "$1"
cd "$W" || { echo "TOLAK rc=2 (worktree $W tak ada)"; exit 2; }
/root/milkyhoop-dev/scripts/ops/cek-sibuk.sh | tail -1 | grep -q "^BEBAS" || { echo "SIBUK -> batal"; echo "TOLAK rc=9"; exit 9; }
[ -z "$(git status --short)" ] || { echo "worktree kotor -> batal"; echo "TOLAK rc=8"; exit 8; }
KEPALA=$(git rev-parse --short HEAD)
echo "== 0 $CABANG @$KEPALA di atas $(git merge-base HEAD deploy/master | cut -c1-9) $(date +%H:%M)"
if [ "${GERBANG_KONTROL_MERAH:-0}" = 1 ]; then
  IFS="|" read -r _n KM_BERKAS KM_EKSPR _t <<< "${SAB[0]}"; sed -i "$KM_EKSPR" "$KM_BERKAS"; echo "KONTROL MERAH: $_n dipasang di $KM_BERKAS"
fi
ringkas() { grep -E "^(FAILED|ERROR) |^=+ .* in " | tail -"${1:-30}"; }
nilai_pytest() {  # $1 label, $2 ringkasan (DI SHELL INI, bukan pipa: tolak di subshell akan hilang); lulus = "N passed" tanpa failed/error
  local r="$2"; echo "$r"
  echo "$r" | grep -qE "^=+ .*[0-9]+ passed" || tolak "$1: tanpa ringkasan lulus"
  echo "$r" | grep -qE "^(FAILED|ERROR) | [0-9]+ (failed|error)" && tolak "$1: ada yang gagal"
  return 0
}
cd backend/api_gateway; F=$(grep -rlE "$POLA_UNIT" tests/unit/*.py | sort -u | tr "\n" " "); cd "$W"
echo "== 1 unit ($(echo $F | wc -w) berkas) $(date +%H:%M)"
R=$(timeout 1500 scripts/jalankan_unit.sh "$W" -q -p no:cacheprovider $F 2>&1 | ringkas); nilai_pytest "unit" "$R"
if [ "${GERBANG_KONTROL_MERAH:-0}" = 1 ]; then  # kontrol merah: cukup langkah 1 (tanpa salinan/suite berat); WAJIB TOLAK
  git checkout -q -- "$KM_BERKAS"; echo "KONTROL MERAH dicabut; worktree bersih: $([ -z "$(git status --short)" ] && echo ya || echo TIDAK)"
  [ ${#TOLAK[@]} -gt 0 ] && { printf 'alasan: %s\n' "${TOLAK[@]}"; echo "TOLAK rc=1"; exit 1; }
  echo "KONTROL MERAH GAGAL: unit rusak tapi gerbang tidak menolak"; echo "LULUS rc=0"; exit 0
fi
echo "== 2 harness $(date +%H:%M)"
POHON="$W" bash "$HARNESS" > /tmp/gerbang-harness.$$ 2>&1
tail -45 /tmp/gerbang-harness.$$
grep -q "^rc=0$" /tmp/gerbang-harness.$$ || tolak "harness: banding rc != 0"
rm -f /tmp/gerbang-harness.$$
echo "== 3 sabotase $(date +%H:%M)"
for s in "${SAB[@]}"; do
  IFS="|" read -r nama berkas ekspr tes <<< "$s"
  sed -i "$ekspr" "$berkas"
  if git diff --quiet -- "$berkas"; then tolak "$nama: sabotase tak mengubah berkas"; continue; fi
  python3 -m py_compile "$berkas" || { tolak "$nama: tak terkompilasi"; git checkout -q -- "$berkas"; continue; }
  hasil=$(timeout 600 scripts/jalankan_unit.sh "$W" -q -p no:cacheprovider $tes 2>&1 | grep -E "^=+ .* in " | tail -1)
  echo "$nama :: $hasil"
  echo "$hasil" | grep -qE "[0-9]+ (failed|error)" || tolak "$nama: sabotase TETAP HIJAU"
  git checkout -q -- "$berkas"
done
echo "== 4 suite $(date +%H:%M)"
R=$(timeout 1800 scripts/jalankan_unit.sh "$W" -q -p no:cacheprovider tests/unit 2>&1 | ringkas 30); nilai_pytest "suite" "$R"
echo "== 5 gate $(date +%H:%M)"
G=$(timeout 600 docker run --rm -i --network milkyhoop_dev_network --env-file /root/milkyhoop-dev/.env -v "$W/backend:/app/backend:ro" -w /app \
  --entrypoint python milkyhoop-dev-api_gateway:latest - < "$W/scripts/gate_izin_rute.py" 2>&1 | tail -6)
echo "$G"; echo "$G" | grep -q "HIJAU" || tolak "gate_izin_rute bukan HIJAU"
P=$(cd /root/milkyhoop-dev && CEK_SAJA=1 bash scripts/ops/window_item.sh "$NAMA_JENDELA" "$W" "$CABANG" "$PENANDA_BERKAS" "$PENANDA" 2>&1 | grep -E "PENANDA|GAGAL|galat" | head -5)
echo "$P"; echo "$P" | grep -q "PENANDA-OK" || tolak "penanda CEK_SAJA tidak OK"
[ -z "$(git status --short)" ] || { tolak "worktree kotor di akhir"; git status --short; }
[ "$(git rev-parse --short HEAD)" = "$KEPALA" ] || tolak "HEAD bergeser selama gerbang"
/root/milkyhoop-dev/scripts/ops/cek-sibuk.sh | tail -1
echo "selesai $(date +%H:%M) $CABANG @$KEPALA"
if [ ${#TOLAK[@]} -eq 0 ]; then echo "LULUS rc=0"; exit 0; fi
printf 'alasan: %s\n' "${TOLAK[@]}"
echo "TOLAK rc=1"; exit 1
