#!/bin/bash
# =============================================================================
# c1_2way.sh — uji DUA ARAH gerbang keselamatan C1, di DB KLON.
# `milkydb` TIDAK DISENTUH sama sekali (semua lewat override DB=).
#
# Uji merah WAJIB membuktikan DROP TIDAK TERJADI — bukan sekadar exit != 0.
# Guard yang menolak SESUDAH menghapus tetap menghapus.
#
# CATATAN untuk pembaca berikutnya: versi pertama skrip UJI ini menyembunyikan
# galat penyiapan fixture (`>/dev/null 2>&1`), sehingga INSERT tenant asing
# gagal diam-diam dan uji merah melaporkan "guard tidak menolak" — padahal
# guard-nya benar dan fixture-nya yang tak pernah ada. Kegagalan ALAT terbaca
# sebagai kegagalan PRODUK. Karena itu di bawah ini setiap penyiapan fixture
# DIVERIFIKASI hasilnya, bukan cuma dijalankan.
# =============================================================================
set -u
DB=${DB:-milkydb_c1test}
C=milkyhoop-dev-postgres-1
R=/root/mh-autofulfill/scripts/e2e/dp_flow/restore_preharness.sh
PASS=0; FAIL=0
ok(){ echo "  PASS — $1"; PASS=$((PASS+1)); }
no(){ echo "  FAIL — $1"; FAIL=$((FAIL+1)); }
Q(){ docker exec -i "$C" psql -U postgres -d "$DB" -tAc "$1" 2>/dev/null | tr -d '[:space:]'; }

mk_tenant(){ # $1 = id ; memverifikasi hasilnya, tidak sekadar menjalankan
  # `alias` WAJIB diisi: ada UNIQUE INDEX "Tenant_alias_key" dan default-nya ''.
  # Tenant KEDUA dengan alias default akan bentrok, dan `ON CONFLICT DO NOTHING`
  # MENELAN bentrokan itu jadi `INSERT 0 0` tanpa galat. Itulah kenapa fixture
  # kedua gagal diam-diam sementara yang pertama sukses (schema-first: cek
  # information_schema/pg_index SEBELUM menulis INSERT).
  docker exec -i "$C" psql -U postgres -d "$DB" -tAc \
    "INSERT INTO \"Tenant\"(id, alias, display_name) VALUES ('$1','$1','$1') ON CONFLICT DO NOTHING;" >/dev/null 2>&1
  local n; n=$(Q "SELECT count(*) FROM \"Tenant\" WHERE id='$1';")
  [ "$n" = "1" ] || { echo "!!! FIXTURE GAGAL: tenant '$1' tak terbuat (count=$n) — ABORT."; exit 2; }
}
tenants(){ Q "SELECT COALESCE(string_agg(id,', ' ORDER BY id),'(kosong)') FROM \"Tenant\";"; }

echo "############ SIAPKAN KLON ($DB) — milkydb tak disentuh ############"
MILKY_TEN_BEFORE=$(docker exec -i "$C" psql -U postgres -d milkydb -tAc "SELECT count(*) FROM \"Tenant\";" | tr -d '[:space:]')
MILKY_JE_BEFORE=$(docker exec -i "$C" psql -U postgres -d milkydb -tAc "SELECT count(*) FROM journal_entries;" | tr -d '[:space:]')
echo "milkydb SEBELUM uji: Tenant=$MILKY_TEN_BEFORE journal=$MILKY_JE_BEFORE"
DB=$DB bash "$R" >/tmp/c1_prep.log 2>&1 || { echo "!!! penyiapan klon gagal"; tail -5 /tmp/c1_prep.log; exit 1; }
echo "klon siap: Tenant=$(Q 'SELECT count(*) FROM "Tenant";') tabel=$(Q "SELECT count(*) FROM information_schema.tables WHERE table_schema='public';")"

echo
echo "############ (d) HIJAU — hanya tenant harness ############"
mk_tenant kaos-biru-konveksi
echo "  tenant sebelum: $(tenants)"
DB=$DB bash "$R" >/tmp/c1_green1.log 2>&1; RC=$?
[ "$RC" = "0" ] && ok "restore JALAN dengan tenant harness saja (rc=0)" || { no "ditolak padahal hanya harness (rc=$RC)"; tail -6 /tmp/c1_green1.log; }
grep -q "PRISTINE OK" /tmp/c1_green1.log && ok "PRISTINE OK tercetak" || no "PRISTINE OK tak tercetak"
[ "$(Q 'SELECT count(*) FROM "Tenant";')" = "0" ] && ok "DROP memang terjadi (1 -> 0)" || no "DROP tak terjadi"

echo
echo "############ (c) MERAH — ada tenant ASING: MENOLAK + NOL DROP ############"
mk_tenant kaos-biru-konveksi
mk_tenant tenant-asing-jangan-hapus
# Penanda yang HANYA bisa selamat kalau DB tidak pernah di-DROP.
docker exec -i "$C" psql -U postgres -d "$DB" -tAc \
  "CREATE TABLE IF NOT EXISTS c1_bukti(x text); INSERT INTO c1_bukti VALUES ('hilang = DROP terjadi');" >/dev/null 2>&1
[ "$(Q 'SELECT count(*) FROM c1_bukti;')" = "1" ] || { echo "!!! FIXTURE penanda gagal — ABORT"; exit 2; }
echo "  tenant sebelum: $(tenants)"
DB=$DB bash "$R" >/tmp/c1_red.log 2>&1; RC=$?
echo "  --- keluaran gerbang ---"; grep -E "MENOLAK|di luar slug|      - " /tmp/c1_red.log | sed 's/^/    /'
[ "$RC" != "0" ] && ok "restore MENOLAK (rc=$RC)" || no "restore JALAN padahal ada tenant asing"
grep -q "MENOLAK" /tmp/c1_red.log && ok "pesan menyebut penolakan" || no "pesan tak menyebut penolakan"
grep -q "tenant-asing-jangan-hapus" /tmp/c1_red.log && ok "tenant asing DISEBUT NAMANYA" || no "tenant asing tak disebut"
[ "$(Q "SELECT count(*) FROM \"Tenant\" WHERE id='tenant-asing-jangan-hapus';")" = "1" ] \
  && ok "★ tenant asing MASIH ADA (nol DROP)" || no "★ tenant asing HILANG — menolak SESUDAH menghapus"
[ "$(Q 'SELECT count(*) FROM c1_bukti;')" = "1" ] \
  && ok "★ tabel penanda selamat — DB tak pernah di-DROP" || no "★ penanda hilang — DB di-DROP"

echo
echo "############ (e) HIJAU — I_KNOW=1 melewati DAN mencetak daftarnya ############"
echo "  tenant sebelum: $(tenants)"
DB=$DB I_KNOW=1 bash "$R" >/tmp/c1_iknow.log 2>&1; RC=$?
echo "  --- keluaran escape hatch ---"; sed -n '/I_KNOW=1 — GUARD DILEWATI/,/^$/p' /tmp/c1_iknow.log | head -12 | sed 's/^/    /'
[ "$RC" = "0" ] && ok "I_KNOW=1 JALAN (rc=0)" || { no "I_KNOW=1 tetap ditolak (rc=$RC)"; tail -6 /tmp/c1_iknow.log; }
grep -q "GUARD DILEWATI DENGAN SENGAJA" /tmp/c1_iknow.log && ok "menyatakan guard dilewati sengaja" || no "tak menyatakan apa pun"
grep -q "tenant-asing-jangan-hapus" /tmp/c1_iknow.log && ok "MENCETAK korban sebelum menghapus" || no "tak mencetak korban"
[ "$(Q "SELECT count(*) FROM \"Tenant\" WHERE id='tenant-asing-jangan-hapus';")" = "0" ] \
  && ok "sesudah I_KNOW=1, DROP memang terjadi" || no "I_KNOW=1 tak jadi menghapus"

echo
echo
echo "############ (f) MERAH — SKEMA RUSAK: guard tak bisa membaca -> WAJIB MENOLAK ############"
# Kasus yang menemukan FAIL-OPEN. Kasus (c)/(d)/(e) semuanya mengandaikan
# tabel "Tenant" BISA DIBACA, jadi tak satu pun menjatuhi beban pada jalur
# "aku tidak tahu". DB yang ada tapi kosong-skema = DB yang restore-nya
# terputus separuh — skenario nyata, bukan karangan.
TDB=milkydb_c1tool
docker exec -i "$C" psql -U postgres -d postgres -tAc "DROP DATABASE IF EXISTS $TDB;" >/dev/null 2>&1
docker exec -i "$C" psql -U postgres -d postgres -tAc "CREATE DATABASE $TDB;" >/dev/null 2>&1
HAS=$(docker exec -i "$C" psql -U postgres -d postgres -tAc "SELECT count(*) FROM pg_database WHERE datname='$TDB';" | tr -d '[:space:]')
[ "$HAS" = "1" ] || { echo "!!! FIXTURE GAGAL: $TDB tak terbuat — ABORT"; exit 2; }
NOTBL=$(docker exec -i "$C" psql -U postgres -d "$TDB" -tAc "SELECT count(*) FROM information_schema.tables WHERE table_schema='public';" | tr -d '[:space:]')
echo "  $TDB ada, tabel public=$NOTBL (0 = skema kosong, tabel Tenant tak terbaca)"
DB=$TDB bash "$R" >/tmp/c1_tool.log 2>&1; RC=$?
echo "  --- keluaran gerbang ---"; grep -E "tak bisa membaca|MENOLAK|kegagalan ALAT" /tmp/c1_tool.log | sed 's/^/    /'
[ "$RC" != "0" ] && ok "skema rusak -> MENOLAK (rc=$RC), bukan lolos ke DROP" || no "★ FAIL-OPEN: guard LOLOS padahal tak bisa membaca Tenant"
grep -q "kegagalan ALAT" /tmp/c1_tool.log && ok "pesan menyebut kegagalan ALAT, bukan kegagalan produk" || no "pesan tak memisahkan alat vs produk"
# I_KNOW=1 TIDAK boleh melewati kasus ini: menyetujui penghapusan yang isinya
# tak bisa dibaca adalah persetujuan tanpa objek.
DB=$TDB I_KNOW=1 bash "$R" >/tmp/c1_tool2.log 2>&1; RC2=$?
[ "$RC2" != "0" ] && ok "I_KNOW=1 TIDAK melewati kegagalan alat (rc=$RC2)" || no "I_KNOW=1 melewati kegagalan alat — persetujuan tanpa objek"
STILL_EMPTY=$(docker exec -i "$C" psql -U postgres -d "$TDB" -tAc "SELECT count(*) FROM information_schema.tables WHERE table_schema='public';" 2>/dev/null | tr -d '[:space:]')
[ "${STILL_EMPTY:-x}" = "0" ] && ok "$TDB tak disentuh (tetap kosong, nol restore)" || no "$TDB berubah — guard sempat bertindak"
docker exec -i "$C" psql -U postgres -d postgres -tAc "DROP DATABASE IF EXISTS $TDB;" >/dev/null 2>&1

echo
echo "############ (g)/(h) MERAH — HARNESS_SLUG unset / kosong ############"
# Tiga kasus pertama mengandaikan slug TERISI. Kalau slug kosong membuat NOL
# tenant terhitung asing, guard jadi fail-open — lebih buruk daripada tak ada
# guard, karena ia memberi rasa aman. Dijawab dengan MENJALANKAN.
mk_tenant kaos-biru-konveksi
mk_tenant tenant-asing-jangan-hapus
echo "  tenant: $(tenants)"
env -u HARNESS_SLUG DB=$DB bash "$R" >/tmp/c1_g.log 2>&1; RCG=$?
[ "$RCG" != "0" ] && ok "(g) HARNESS_SLUG UNSET -> tetap MENOLAK (fail-closed)" || no "(g) UNSET -> LOLOS (fail-open)"
HARNESS_SLUG="" DB=$DB bash "$R" >/tmp/c1_h.log 2>&1; RCH=$?
[ "$RCH" != "0" ] && ok "(h) HARNESS_SLUG=\"\" -> tetap MENOLAK (fail-closed)" || no "(h) kosong -> LOLOS (fail-open)"
echo "  (g) menyebut: $(grep -oE "slug harness '[^']*'" /tmp/c1_g.log | head -1)"
echo "  (h) menyebut: $(grep -oE "slug harness '[^']*'" /tmp/c1_h.log | head -1)"
[ "$(Q "SELECT count(*) FROM \"Tenant\" WHERE id='tenant-asing-jangan-hapus';")" = "1" ] \
  && ok "tenant asing tetap selamat sesudah (g)+(h)" || no "tenant asing hilang di (g)/(h)"

echo "############ BERSIH-BERSIH + BUKTI milkydb UTUH ############"
docker exec -i "$C" psql -U postgres -d postgres -tAc "ALTER DATABASE $DB WITH ALLOW_CONNECTIONS false;" >/dev/null 2>&1
docker exec -i "$C" psql -U postgres -d postgres -tAc "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname='$DB' AND pid<>pg_backend_pid();" >/dev/null 2>&1
docker exec -i "$C" psql -U postgres -d postgres -tAc "DROP DATABASE $DB;" >/dev/null 2>&1
LEFT=$(docker exec -i "$C" psql -U postgres -d postgres -tAc "SELECT count(*) FROM pg_database WHERE datname='$DB';" | tr -d '[:space:]')
[ "$LEFT" = "0" ] && ok "DB uji dihapus (self-clean)" || no "DB uji tersisa"
MILKY_TEN_AFTER=$(docker exec -i "$C" psql -U postgres -d milkydb -tAc "SELECT count(*) FROM \"Tenant\";" | tr -d '[:space:]')
MILKY_JE_AFTER=$(docker exec -i "$C" psql -U postgres -d milkydb -tAc "SELECT count(*) FROM journal_entries;" | tr -d '[:space:]')
echo "  milkydb SESUDAH: Tenant=$MILKY_TEN_AFTER journal=$MILKY_JE_AFTER"
[ "$MILKY_TEN_AFTER" = "$MILKY_TEN_BEFORE" ] && ok "milkydb Tenant tak berubah ($MILKY_TEN_BEFORE)" || no "milkydb Tenant BERUBAH $MILKY_TEN_BEFORE -> $MILKY_TEN_AFTER"
[ "$MILKY_JE_AFTER" = "$MILKY_JE_BEFORE" ] && ok "milkydb journal tak berubah ($MILKY_JE_BEFORE)" || no "milkydb journal BERUBAH"

echo
echo "=============================================="
echo " C1 DUA ARAH: PASS=$PASS  FAIL=$FAIL"
echo "=============================================="
[ "$FAIL" -eq 0 ] || exit 1
