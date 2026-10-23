#!/usr/bin/env bash
# Uji V444 (semai peran lengkap) di DB SCRATCH: klon SKEMA milkydb + data "Tenant"/chart_of_accounts/account_roles. Prod TIDAK ditulis.
# Bukti: kontrol MERAH anchor hilang -> V444 gagal keras & fungsi pulih identik; terapan -> matriks celah 0, grapgrap tak berubah (penjaga
# di migrasi), diff fungsi hanya baris dimaksud; tenant BARU dummy mendapat 1-10450 + 4 peran; terapan ulang = no-op; ROLLBACK memulihkan
# fungsi identik + celah kembali persis; terapan ulang sesudah rollback lolos. DB scratch dihapus dan dibuktikan hilang.
set -u
P=milkyhoop-dev-postgres-1; S=scratch_v444_$(date +%s); M=${M:-$(cd "$(dirname "$0")/../.." && pwd)/backend/migrations}
V=$M/V444__peran_semai_lengkap.sql; RB=$M/V444__peran_semai_lengkap_ROLLBACK.sql
D=$(mktemp -d /root/.v444-XXXX); gagal=0
Q() { docker exec -i $P psql -U postgres -d $S -v ON_ERROR_STOP=1 -At "$@"; }
F() { echo "FAIL $*"; gagal=1; }
hapus() { docker exec $P psql -U postgres -At -c "DROP DATABASE IF EXISTS $S" >/dev/null;
          echo "scratch dihapus; sisa DB scratch_v444: $(docker exec $P psql -U postgres -At -c "SELECT count(*) FROM pg_database WHERE datname LIKE 'scratch_v444%'")"; rm -rf $D; }
trap hapus EXIT
[ -f $V ] && [ -f $RB ] || { echo "FAIL berkas migrasi tak ada di $M"; exit 2; }
docker exec $P psql -U postgres -At -c "CREATE DATABASE $S" >/dev/null || exit 2
docker exec $P sh -c "pg_dump -U postgres -d milkydb --schema-only --no-owner --no-acl | psql -q -U postgres -d $S" >/dev/null 2>&1
docker exec $P sh -c "pg_dump -U postgres -d milkydb --data-only --no-owner --no-acl --disable-triggers -t '\"Tenant\"' -t chart_of_accounts -t account_roles | psql -q -U postgres -d $S" >/dev/null 2>&1
CELAH="SELECT t.id || ':' || string_agg(p.r, ',' ORDER BY p.r) FROM \"Tenant\" t CROSS JOIN (VALUES ('EMPLOYEE_ADVANCE'),('BANK_ADJUSTMENT_GAIN'),('BANK_ADJUSTMENT_LOSS'),('REVENUE_SALES_SERVICE')) p(r) WHERE NOT EXISTS (SELECT 1 FROM account_roles a WHERE a.tenant_id=t.id AND a.role_key=p.r) GROUP BY t.id ORDER BY t.id"
MD5GG="SELECT md5(string_agg(role_key||':'||account_id::text, '|' ORDER BY role_key)) || '/' || (SELECT md5(string_agg(id::text||account_code, '|' ORDER BY account_code, id)) FROM chart_of_accounts WHERE tenant_id='grapgrap-manado') FROM account_roles WHERE tenant_id='grapgrap-manado'"
echo "disalin: tenant=$(Q -c 'SELECT count(*) FROM "Tenant"') coa=$(Q -c 'SELECT count(*) FROM chart_of_accounts') peran=$(Q -c 'SELECT count(*) FROM account_roles')"
Q -c "$CELAH" > $D/celah_sebelum; echo "celah sebelum:"; sed 's/^/    /' $D/celah_sebelum
[ -s $D/celah_sebelum ] || F "celah sebelum kosong (salinan data gagal?)"
grep -q "^grapgrap-manado:" $D/celah_sebelum && F "grapgrap-manado punya celah (diukur prod: lengkap)"
GG0=$(Q -c "$MD5GG")
FN="seed_default_coa(text) seed_default_account_roles(text)"
for f in $FN; do Q -c "SELECT pg_get_functiondef('$f'::regprocedure)" > "$D/sebelum_${f%%(*}.sql"; done
# MERAH: anchor AR_TRADE dirusak -> V444 WAJIB gagal keras, transaksi tergulung
sed "s/('AR_TRADE',  /('AR_TRADEX', /" "$D/sebelum_seed_default_account_roles.sql" > $D/rusak.sql
grep -q "AR_TRADEX" $D/rusak.sql || { echo "FAIL MERAH: sabotase tak terpasang"; exit 1; }
Q -1 < $D/rusak.sql >/dev/null
if Q -1 < $V >/dev/null 2>$D/e1; then F "MERAH: V444 lolos padahal anchor AR_TRADE hilang"
else echo "PASS MERAH: V444 gagal keras ($(grep -oE 'V444[^"]*' $D/e1 | head -1))"; fi
Q -1 < "$D/sebelum_seed_default_account_roles.sql" >/dev/null
Q -c "SELECT pg_get_functiondef('seed_default_account_roles(text)'::regprocedure)" | diff -q - "$D/sebelum_seed_default_account_roles.sql" >/dev/null \
  && echo "PASS fungsi dipulihkan identik sesudah merah" || F "pemulihan fungsi sesudah merah"
[ "$(Q -c "SELECT count(*) FROM chart_of_accounts WHERE description LIKE 'V444%'")" = 0 ] && echo "PASS merah tergulung (0 akun V444)" || F "merah tak tergulung"
# TERAPAN
Q -1 < $V >/dev/null 2>$D/err && echo "PASS V444 terapan" || { F "V444: $(tail -3 $D/err)"; exit 1; }
for f in $FN; do
  Q -c "SELECT pg_get_functiondef('$f'::regprocedure)" > "$D/sesudah_${f%%(*}.sql"
  echo "--- diff ${f%%(*}:"; diff "$D/sebelum_${f%%(*}.sql" "$D/sesudah_${f%%(*}.sql" | grep -E "^[<>]" | sed 's/^/    /'
done
[ -z "$(Q -c "$CELAH")" ] && echo "PASS celah sesudah = 0" || F "celah sesudah: $(Q -c "$CELAH")"
[ "$(Q -c "$MD5GG")" = "$GG0" ] && echo "PASS grapgrap-manado peran+CoA md5 sama" || F "grapgrap berubah"
echo "akun 1-10450 baru (V444)=$(Q -c "SELECT count(*) FROM chart_of_accounts WHERE description LIKE 'V444%'") peran V444=$(Q -c "SELECT count(*) FROM account_roles WHERE notes LIKE 'V444%'")"
# tenant BARU dummy
Q -c "INSERT INTO \"Tenant\" SELECT (jsonb_populate_record(NULL::\"Tenant\", to_jsonb(t) || jsonb_build_object('id', 'zz-scratch-v444', 'alias', 'zz-scratch-v444'))).* FROM \"Tenant\" t LIMIT 1" >/dev/null 2>$D/e2 || F "tenant dummy: $(tail -2 $D/e2)"
Q -c "SELECT seed_default_coa('zz-scratch-v444')" >/dev/null 2>>$D/e2 && Q -c "SELECT seed_default_account_roles('zz-scratch-v444')" >/dev/null 2>>$D/e2 || F "seed tenant baru: $(tail -3 $D/e2)"
NB=$(Q -c "SELECT count(*) FROM account_roles a JOIN chart_of_accounts c ON c.id=a.account_id WHERE a.tenant_id='zz-scratch-v444' AND (a.role_key, c.account_code) IN (('EMPLOYEE_ADVANCE','1-10450'),('BANK_ADJUSTMENT_GAIN','4-90000'),('BANK_ADJUSTMENT_LOSS','5-20900'),('REVENUE_SALES_SERVICE','4-10150'))")
[ "$NB" = 4 ] && echo "PASS tenant baru: 4 peran dgn kode bawaan + 1-10450 ($(Q -c "SELECT count(*) FROM chart_of_accounts WHERE tenant_id='zz-scratch-v444' AND account_code='1-10450'"))" || F "tenant baru peran=$NB"
Q -c "DELETE FROM account_roles WHERE tenant_id='zz-scratch-v444'; DELETE FROM chart_of_accounts WHERE tenant_id='zz-scratch-v444'; DELETE FROM \"Tenant\" WHERE id='zz-scratch-v444'" >/dev/null
# idempoten
Q -1 < $V >/dev/null 2>$D/e5 && echo "PASS V444 diulang = no-op lolos" || F "V444 diulang: $(tail -2 $D/e5)"
# ROLLBACK
Q -1 < $RB >/dev/null 2>$D/e3 && echo "PASS ROLLBACK" || F "ROLLBACK: $(tail -3 $D/e3)"
for f in $FN; do
  Q -c "SELECT pg_get_functiondef('$f'::regprocedure)" > "$D/rb_${f%%(*}.sql"
  diff -q "$D/sebelum_${f%%(*}.sql" "$D/rb_${f%%(*}.sql" >/dev/null && echo "PASS rollback memulihkan ${f%%(*} identik" || F "rollback ${f%%(*} tak identik"
done
Q -c "$CELAH" | diff -q - $D/celah_sebelum >/dev/null && echo "PASS celah sesudah rollback == sebelum" || F "celah sesudah rollback berbeda"
[ "$(Q -c "$MD5GG")" = "$GG0" ] && echo "PASS grapgrap md5 sama sesudah rollback" || F "grapgrap berubah sesudah rollback"
Q -1 < $V >/dev/null 2>$D/e6 && echo "PASS terapan ulang sesudah rollback" || F "terapan ulang: $(tail -2 $D/e6)"
echo "RINGKAS gagal=$gagal"; exit $gagal
