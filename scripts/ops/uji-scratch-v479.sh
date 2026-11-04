#!/usr/bin/env bash
# Uji V479 (pembulatan pembelian c-2) di DB SCRATCH: klon SKEMA + data "Tenant"/chart_of_accounts/account_roles/tenant_features/bills/
# vendor_credits. Prod TIDAK ditulis. Bukti: MERAH (5-90100 dipakai akun lain) -> gagal keras + tergulung; terapan -> 5-90100 + peran di
# setiap tenant ber-5-90000, kolom pembulatan, beku Law 19 memuat pembulatan (posted ditolak, draf boleh); semai tenant (CoA + peran dihapus
# lalu disemai ulang) melahirkan 5-90100 + PURCHASE_ROUNDING; terapan ulang = no-op; ROLLBACK memulihkan trigger + fungsi semai IDENTIK.
# Scratch dihapus & dibuktikan hilang.
set -u
P=milkyhoop-dev-postgres-1; S=scratch_v479_$(date +%s); M=${M:-$(cd "$(dirname "$0")/../.." && pwd)/backend/migrations}
V=$M/V479__pembulatan_pembelian.sql; RB=$M/V479__pembulatan_pembelian_ROLLBACK.sql
D=$(mktemp -d /root/.v479-XXXX); gagal=0
Q() { docker exec -i $P psql -U postgres -d $S -v ON_ERROR_STOP=1 -At "$@"; }
F() { echo "FAIL $*"; gagal=1; }
hapus() { docker exec $P psql -U postgres -At -c "DROP DATABASE IF EXISTS $S" >/dev/null;
          echo "scratch dihapus; sisa DB scratch_v479: $(docker exec $P psql -U postgres -At -c "SELECT count(*) FROM pg_database WHERE datname LIKE 'scratch_v479%'")"; rm -rf $D; }
trap hapus EXIT
[ -f $V ] && [ -f $RB ] || { echo "FAIL berkas migrasi tak ada di $M"; exit 2; }
docker exec $P psql -U postgres -At -c "CREATE DATABASE $S" >/dev/null || exit 2
docker exec $P sh -c "pg_dump -U postgres -d milkydb --schema-only --no-owner --no-acl | psql -q -U postgres -d $S" >/dev/null 2>&1
docker exec $P sh -c "pg_dump -U postgres -d milkydb --data-only --no-owner --no-acl --disable-triggers -t '\"Tenant\"' -t chart_of_accounts -t account_roles -t tenant_features -t bills -t vendor_credits | psql -q -U postgres -d $S" >/dev/null 2>&1
N0=$(Q -c "SELECT count(*) FROM chart_of_accounts WHERE account_code='5-90000' AND account_type='OTHER_EXPENSE' AND NOT is_header")
echo "disalin: tenant=$(Q -c 'SELECT count(*) FROM "Tenant"') coa=$(Q -c 'SELECT count(*) FROM chart_of_accounts') ber-5-90000=$N0 bills=$(Q -c 'SELECT count(*) FROM bills')"
TG() { Q -c "SELECT pg_get_triggerdef(t.oid) FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid WHERE c.relname='bills' AND t.tgname='trg_law19_bekukan_nominal'"; }
TG > $D/tg0; Q -c "SELECT pg_get_functiondef('seed_default_coa(text)'::regprocedure)" > $D/sc0
Q -c "SELECT pg_get_functiondef('seed_default_account_roles(text)'::regprocedure)" > $D/sr0
# MERAH: 5-90100 dipakai akun lain di satu tenant -> V479 gagal keras, tergulung
T1=$(Q -c "SELECT tenant_id FROM chart_of_accounts WHERE account_code='5-90000' ORDER BY tenant_id LIMIT 1")
Q -c "INSERT INTO chart_of_accounts (tenant_id, account_code, name, account_type, normal_balance) VALUES ('$T1','5-90100','Akun Lain','EXPENSE','DEBIT')" >/dev/null
if Q -1 < $V >/dev/null 2>$D/e1; then F "MERAH: V479 lolos padahal 5-90100 dipakai akun lain"; else echo "PASS MERAH: V479 gagal keras ($(grep -oE 'V479[^"]*' $D/e1 | head -1 | cut -c1-80))"; fi
[ "$(Q -c "SELECT count(*) FROM information_schema.columns WHERE table_name='bills' AND column_name='pembulatan'")" = 0 ] && echo "PASS merah tergulung (kolom tak ada)" || F "merah tak tergulung"
Q -c "DELETE FROM chart_of_accounts WHERE tenant_id='$T1' AND account_code='5-90100'" >/dev/null
# TERAPAN
Q -1 < $V > $D/o2 2>&1 && echo "PASS V479 terapan ($(grep -c NOTICE $D/o2) NOTICE dilewati)" || { F "V479: $(tail -3 $D/o2)"; exit 1; }
N1=$(Q -c "SELECT count(*) FROM account_roles a JOIN chart_of_accounts c ON c.id=a.account_id WHERE a.role_key='PURCHASE_ROUNDING' AND c.account_code='5-90100' AND c.name='Pembulatan Pembelian' AND c.account_type='OTHER_EXPENSE' AND c.parent_code='5-90000'")
[ "$N1" = "$N0" ] && echo "PASS peran PURCHASE_ROUNDING -> 5-90100 di $N1/$N0 tenant" || F "peran $N1 <> $N0"
[ "$(Q -c "SELECT count(*) FROM information_schema.columns WHERE table_name IN ('bills','vendor_credits') AND column_name='pembulatan' AND numeric_scale=2 AND is_nullable='NO'")" = 2 ] && echo "PASS kolom pembulatan x2" || F "kolom"
TG | grep -q "'pembulatan'" && TG | grep -q "'pph_dpp'" && echo "PASS beku Law 19 += pembulatan (kolom lama tetap)" || F "trigger: $(TG)"
BP=$(Q -c "SELECT id FROM bills WHERE journal_id IS NOT NULL LIMIT 1"); BD=$(Q -c "SELECT id FROM bills WHERE journal_id IS NULL LIMIT 1")
if [ -n "$BP" ]; then Q -c "UPDATE bills SET pembulatan=0.5 WHERE id='$BP'" >/dev/null 2>$D/e3 && F "pembulatan tagihan POSTED bisa diubah" || echo "PASS pembulatan posted beku"; else echo "SKIP tak ada tagihan posted"; fi
if [ -n "$BD" ]; then Q -c "BEGIN; UPDATE bills SET pembulatan=0.5, grand_total=grand_total WHERE id='$BD'; ROLLBACK" >/dev/null 2>$D/e4 && echo "PASS draf boleh ditulis pembulatan" || F "draf: $(tail -2 $D/e4)"; else echo "SKIP tak ada draf"; fi
# SEMAI tenant: hapus CoA + peran satu tenant lalu semai ulang dengan fungsi yang sudah ditambal
Q -c "DELETE FROM account_roles WHERE tenant_id='$T1'; DELETE FROM chart_of_accounts WHERE tenant_id='$T1'" >/dev/null
Q -c "SELECT seed_default_coa('$T1')" > $D/n1 2>$D/e5 && Q -c "SELECT seed_default_account_roles('$T1')" >/dev/null 2>>$D/e5 || F "semai: $(tail -3 $D/e5)"
[ "$(Q -c "SELECT count(*) FROM account_roles a JOIN chart_of_accounts c ON c.id=a.account_id WHERE a.tenant_id='$T1' AND a.role_key='PURCHASE_ROUNDING' AND c.account_code='5-90100'")" = 1 ] \
  && [ "$(Q -c "SELECT count(*) FROM chart_of_accounts WHERE tenant_id='$T1'")" = "$(cat $D/n1)" ] \
  && echo "PASS semai baru: 5-90100 + PURCHASE_ROUNDING (v_count == jumlah akun $(cat $D/n1))" || F "semai: n=$(cat $D/n1) akun=$(Q -c "SELECT count(*) FROM chart_of_accounts WHERE tenant_id='$T1'")"
# TERAPAN ULANG = no-op
Q -1 < $V >/dev/null 2>$D/e6 && echo "PASS V479 diulang lolos (idempoten)" || F "ulang: $(tail -2 $D/e6)"
# ROLLBACK
Q -1 < $RB >/dev/null 2>$D/e7 && echo "PASS ROLLBACK" || F "ROLLBACK: $(tail -3 $D/e7)"
TG | diff -q - $D/tg0 >/dev/null && echo "PASS rollback trigger identik" || F "rollback trigger beda"
Q -c "SELECT pg_get_functiondef('seed_default_coa(text)'::regprocedure)" | diff -q - $D/sc0 >/dev/null && echo "PASS rollback seed_default_coa identik" || F "rollback seed_default_coa beda"
Q -c "SELECT pg_get_functiondef('seed_default_account_roles(text)'::regprocedure)" | diff -q - $D/sr0 >/dev/null && echo "PASS rollback seed_default_account_roles identik" || F "rollback seed roles beda"
[ "$(Q -c "SELECT count(*) FROM account_roles WHERE role_key='PURCHASE_ROUNDING'")$(Q -c "SELECT count(*) FROM chart_of_accounts WHERE account_code='5-90100'")$(Q -c "SELECT count(*) FROM information_schema.columns WHERE column_name='pembulatan' AND table_name IN ('bills','vendor_credits')")" = 000 ] \
  && echo "PASS rollback: peran, akun, kolom hilang" || F "rollback sisa"
echo "RINGKAS gagal=$gagal"; exit $gagal
