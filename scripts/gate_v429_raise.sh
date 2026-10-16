#!/usr/bin/env bash
# Gerbang V429 (pesan RAISE DB -> Indonesia). DB scratch milkydb_v429 = skema milkydb + baris "Tenant" (TAK menulis milkydb).
# Memeriksa: sidik md5 sebelum = lama; sesudah V429 = baru; badan lama vs baru berbeda HANYA literal (scripts/ops/raise_id.py);
# pesan nyata (generate_efaktur_number tanpa rentang NSFP) berbahasa Indonesia; ulang V429 aman; ROLLBACK = definisi lama
# byte; penjaga drift menolak definisi asing. --tanpa-migrasi = KONTROL MERAH (harus GAGAL).
set -uo pipefail
P=milkyhoop-dev-postgres-1; DB=milkydb_v429
AKAR="$(cd "$(dirname "$0")/.." && pwd)"; M=$AKAR/backend/migrations; RZ=$AKAR/scripts/ops/raise_id.py
q() { docker exec -i "$P" psql -U postgres "$@"; }
gagal=0; cek() { if [ "$1" = 0 ]; then echo "PASS $2"; else echo "FAIL $2"; gagal=1; fi; }
FN="'prevent_posted_journal_update','prevent_posted_journal_delete','prevent_posted_journal_line_modification','prevent_sr_modification','guard_opening_balance','guard_arap_requires_obligation','guard_journal_line_header_balance','generate_efaktur_number','trg_validate_bill_status_v2','trg_validate_expense_status'"
SIDIK="SELECT p.oid::regprocedure::text || '|' || md5(pg_get_functiondef(p.oid)) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='public' AND p.proname IN ($FN) ORDER BY 1"
DEF="SELECT string_agg(pg_get_functiondef(p.oid), E'\n' ORDER BY p.proname) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='public' AND p.proname IN ($FN)"
sidik_dari() { grep -oE "\('[^']+\(\)?[^']*', '[0-9a-f]{32}', '[0-9a-f]{32}'\)" "$M/V429__pesan_raise_db_indonesia.sql" | sed -E "s/\('([^']+)', '([0-9a-f]+)', '([0-9a-f]+)'\)/\1|\\$1/" ; }
harap() { grep -oE "\('[^']+', '[0-9a-f]{32}', '[0-9a-f]{32}'\)" "$M/V429__pesan_raise_db_indonesia.sql" | sed -E "s/\('([^']+)', '([0-9a-f]+)', '([0-9a-f]+)'\)/\1|\\$1/" | sort; }
lama_sidik() { harap 2; }; baru_sidik() { harap 3; }
efaktur() { q -d "$DB" -At -c "SELECT generate_efaktur_number('kaos-biru-konveksi')" 2>&1 | grep -oE "ERROR: .*" | head -1; }

q -d postgres -qc "DROP DATABASE IF EXISTS $DB" -c "CREATE DATABASE $DB" 2>/dev/null || exit 2
docker exec "$P" sh -c "pg_dump -U postgres -s milkydb | psql -U postgres -d $DB -q -o /dev/null" 2>/dev/null
docker exec "$P" sh -c "pg_dump -U postgres --data-only -t 'public.\"Tenant\"' milkydb | psql -U postgres -d $DB -q -o /dev/null" || exit 2
[ "$(q -d "$DB" -At -c "$SIDIK" | sort)" = "$(lama_sidik)" ]; cek $? "sidik sebelum = definisi lama yang dikenal migrasi (10 fungsi)"
q -d "$DB" -At -c "$DEF" > /tmp/v429_def_lama.sql
if [ "${1:-}" != "--tanpa-migrasi" ]; then
  q -d "$DB" -v ON_ERROR_STOP=1 -q -1 < "$M/V429__pesan_raise_db_indonesia.sql" 2>/dev/null; cek $? "V429 diterapkan (satu transaksi)"
fi
[ "$(q -d "$DB" -At -c "$SIDIK" | sort)" = "$(baru_sidik)" ]; cek $? "sidik sesudah = definisi baru"
q -d "$DB" -At -c "$DEF" > /tmp/v429_def_baru.sql
python3 - "$RZ" <<'PY'; cek $? "badan lama vs baru berbeda HANYA literal pesan (15 pesan)"
import importlib.util, sys
s = importlib.util.spec_from_file_location("rz", sys.argv[1]); rz = importlib.util.module_from_spec(s); s.loader.exec_module(rz)
lama, baru = open("/tmp/v429_def_lama.sql").read(), open("/tmp/v429_def_baru.sql").read()
assert lama != baru
assert rz.normal_tanpa_pesan(lama, 0) == rz.normal_tanpa_pesan(baru, 1)
assert all(f"'{b}'" in baru and f"'{a}'" not in baru for a, b in rz.PETA)
PY
e=$(efaktur); echo "  pesan nyata: $e"
echo "$e" | grep -q "Belum ada rentang nomor e-Faktur yang aktif"; cek $? "pesan nyata generate_efaktur_number berbahasa Indonesia"
if [ "${1:-}" != "--tanpa-migrasi" ]; then
  q -d "$DB" -v ON_ERROR_STOP=1 -q -1 < "$M/V429__pesan_raise_db_indonesia.sql" 2>/dev/null; cek $? "V429 diulang aman (penjaga menerima definisi baru)"
  q -d "$DB" -v ON_ERROR_STOP=1 -q -1 < "$M/V429__pesan_raise_db_indonesia_ROLLBACK.sql" 2>/dev/null; cek $? "ROLLBACK diterapkan"
  [ "$(q -d "$DB" -At -c "$SIDIK" | sort)" = "$(lama_sidik)" ]; cek $? "sesudah ROLLBACK sidik = definisi lama (byte)"
  e2=$(efaktur); echo "  pesan sesudah ROLLBACK: $e2"
  echo "$e2" | grep -q "No active e-Faktur sequence"; cek $? "sesudah ROLLBACK pesan kembali Inggris"
  q -d "$DB" -qc "CREATE OR REPLACE FUNCTION public.trg_validate_expense_status() RETURNS trigger LANGUAGE plpgsql AS \$f\$ BEGIN RETURN NEW; END \$f\$" >/dev/null
  q -d "$DB" -v ON_ERROR_STOP=1 -q -1 < "$M/V429__pesan_raise_db_indonesia.sql" > /tmp/v429_drift.log 2>&1
  [ $? != 0 ] && grep -q "menyimpang" /tmp/v429_drift.log; cek $? "penjaga drift menolak definisi asing (tanpa menimpa)"
fi
q -d postgres -qc "DROP DATABASE IF EXISTS $DB"
rm -f /tmp/v429_def_*.sql /tmp/v429_drift.log
[ $gagal = 0 ] && { echo LULUS; exit 0; }; echo GAGAL; exit 1
