#!/usr/bin/env bash
# Uji V452 (GRN, dulu V439) di DB SCRATCH: klon SKEMA milkydb + data chart_of_accounts/account_roles/journal_source_types.
# Syarat MASTER: diff badan fungsi yang ditambal SEBELUM vs SESUDAH (hanya baris dimaksud), ROLLBACK, kontrol merah anchor hilang,
# DB scratch dihapus di akhir dan dibuktikan hilang. Prod TIDAK ditulis.
set -u
P=milkyhoop-dev-postgres-1; S=scratch_v452_$(date +%s); M=${M:-/root/mh-grn/backend/migrations}
V=$M/V452__penerimaan_barang_grni.sql; RB=$M/V452__penerimaan_barang_grni_ROLLBACK.sql
D=$(mktemp -d /root/.v452-XXXX)
Q() { docker exec -i $P psql -U postgres -d $S -v ON_ERROR_STOP=1 -At "$@"; }
hapus() { docker exec $P psql -U postgres -At -c "DROP DATABASE IF EXISTS $S" >/dev/null;
          echo "scratch dihapus; sisa DB scratch_v452: $(docker exec $P psql -U postgres -At -c "SELECT count(*) FROM pg_database WHERE datname LIKE 'scratch_v452%'")"; rm -rf $D; }
trap hapus EXIT
[ -f $V ] || { echo "FAIL berkas $V tak ada"; exit 2; }
docker exec $P psql -U postgres -At -c "CREATE DATABASE $S" >/dev/null || exit 2
docker exec $P sh -c "pg_dump -U postgres -d milkydb --schema-only --no-owner --no-acl | psql -q -U postgres -d $S" >/dev/null 2>&1
docker exec $P sh -c "pg_dump -U postgres -d milkydb --data-only --no-owner --no-acl --disable-triggers -t '\"Tenant\"' -t chart_of_accounts -t account_roles -t journal_source_types | psql -q -U postgres -d $S" >/dev/null 2>&1
echo "disalin: tenant_rows=$(Q -c 'SELECT count(*) FROM "Tenant"') coa=$(Q -c 'SELECT count(*) FROM chart_of_accounts') peran=$(Q -c 'SELECT count(*) FROM account_roles') source_types=$(Q -c 'SELECT count(*) FROM journal_source_types') tenant=$(Q -c "SELECT count(DISTINCT tenant_id) FROM chart_of_accounts WHERE account_code='2-10200'")"
FN="is_effective_journal(uuid) seed_default_coa(text) seed_default_account_roles(text)"
for f in $FN; do Q -c "SELECT pg_get_functiondef('$f'::regprocedure)" > "$D/sebelum_${f%%(*}.sql"; done
# kontrol merah PADA FUNGSI ASLI (sebelum terapan): anchor AP_TRADE seed_default_account_roles dirusak -> V452 WAJIB gagal keras
sed "s/('AP_TRADE',  /('AP_TRADEX', /" "$D/sebelum_seed_default_account_roles.sql" > $D/rusak.sql
grep -q "AP_TRADEX" $D/rusak.sql || { echo "FAIL MERAH: sabotase tak terpasang (anchor tak ditemukan)"; exit 1; }
Q -1 < $D/rusak.sql >/dev/null
if Q -1 < $V >/dev/null 2>$D/e4; then echo "FAIL MERAH: V452 lolos padahal anchor AP_TRADE hilang"; exit 1
else echo "PASS MERAH: V452 gagal keras ($(grep -oE 'V4(39|52)[^\"]*' $D/e4 | head -1))"; fi
Q -1 < "$D/sebelum_seed_default_account_roles.sql" >/dev/null
Q -c "SELECT pg_get_functiondef('seed_default_account_roles(text)'::regprocedure)" | diff -q - "$D/sebelum_seed_default_account_roles.sql" >/dev/null \
  && echo "PASS fungsi dipulihkan identik sesudah merah" || { echo "FAIL pemulihan fungsi"; exit 1; }
echo "sesudah merah (transaksi V452 tergulung): akun2-10150=$(Q -c "SELECT count(*) FROM chart_of_accounts WHERE account_code='2-10150'") tabel=$(Q -c "SELECT count(*) FROM information_schema.tables WHERE table_name LIKE 'goods_receipt%'")"
Q -1 < $V >/dev/null 2>$D/err && echo "PASS V452 terapan" || { echo "FAIL V452: $(tail -3 $D/err)"; exit 1; }
for f in $FN; do
  Q -c "SELECT pg_get_functiondef('$f'::regprocedure)" > "$D/sesudah_${f%%(*}.sql"
  echo "--- diff ${f%%(*} (hanya baris dimaksud):"; diff "$D/sebelum_${f%%(*}.sql" "$D/sesudah_${f%%(*}.sql" | grep -E "^[<>]" | sed 's/^/    /'
done
echo "akun 2-10150=$(Q -c "SELECT count(*) FROM chart_of_accounts WHERE account_code='2-10150' AND account_type='LIABILITY'") 5-10400=$(Q -c "SELECT count(*) FROM chart_of_accounts WHERE account_code='5-10400'") peran GRNI=$(Q -c "SELECT count(*) FROM account_roles WHERE role_key='GOODS_RECEIVED_NOT_INVOICED'") PPV=$(Q -c "SELECT count(*) FROM account_roles WHERE role_key='PURCHASE_PRICE_VARIANCE'")"
echo "source_types GOODS_RECEIPT*=$(Q -c "SELECT count(*) FROM journal_source_types WHERE source_type LIKE 'GOODS_RECEIPT%'") tabel=$(Q -c "SELECT count(*) FROM information_schema.tables WHERE table_name IN ('goods_receipts','goods_receipt_items','goods_receipt_bill_links')")"
# tenant baru dummy: baris "Tenant" salinan satu baris dengan id baru (scratch saja)
Q -c "INSERT INTO \"Tenant\" SELECT (jsonb_populate_record(NULL::\"Tenant\", to_jsonb(t) || jsonb_build_object('id', 'zz-scratch-v452', 'alias', 'zz-scratch-v452'))).* FROM \"Tenant\" t LIMIT 1" >/dev/null 2>$D/e2 || echo "INFO tenant dummy gagal: $(tail -2 $D/e2)"
Q -c "SELECT seed_default_coa('zz-scratch-v452')" >/dev/null 2>>$D/e2 && Q -c "SELECT seed_default_account_roles('zz-scratch-v452')" >/dev/null 2>>$D/e2 \
  && echo "tenant baru: 2-10150=$(Q -c "SELECT count(*) FROM chart_of_accounts WHERE tenant_id='zz-scratch-v452' AND account_code='2-10150'") 5-10400=$(Q -c "SELECT count(*) FROM chart_of_accounts WHERE tenant_id='zz-scratch-v452' AND account_code='5-10400'") peran GRNI=$(Q -c "SELECT count(*) FROM account_roles WHERE tenant_id='zz-scratch-v452' AND role_key='GOODS_RECEIVED_NOT_INVOICED'") PPV=$(Q -c "SELECT count(*) FROM account_roles WHERE tenant_id='zz-scratch-v452' AND role_key='PURCHASE_PRICE_VARIANCE'")" \
  || echo "FAIL seed tenant baru: $(tail -3 $D/e2)"
grep -q "GOODS_RECEIPT_REVERSAL" "$D/sesudah_is_effective_journal.sql" && echo "PASS is_effective_journal memuat GOODS_RECEIPT_REVERSAL" || echo "FAIL is_effective_journal"
Q -1 < $V >/dev/null 2>&1 && echo "INFO V452 diulang: lolos" || echo "INFO V452 diulang: GAGAL (CREATE TABLE tanpa IF NOT EXISTS -> migrasi sekali-jalan; apply_mig menjalankan sekali)"
Q -1 < $RB >/dev/null 2>$D/e3 && echo "PASS ROLLBACK" || echo "FAIL ROLLBACK: $(tail -3 $D/e3)"
echo "sesudah rollback: tabel=$(Q -c "SELECT count(*) FROM information_schema.tables WHERE table_name LIKE 'goods_receipt%'") akun2-10150=$(Q -c "SELECT count(*) FROM chart_of_accounts WHERE account_code='2-10150'") peran=$(Q -c "SELECT count(*) FROM account_roles WHERE role_key IN ('GOODS_RECEIVED_NOT_INVOICED','PURCHASE_PRICE_VARIANCE')") source=$(Q -c "SELECT count(*) FROM journal_source_types WHERE source_type LIKE 'GOODS_RECEIPT%'")"
for f in $FN; do
  Q -c "SELECT pg_get_functiondef('$f'::regprocedure)" > "$D/rb_${f%%(*}.sql"
  diff -q "$D/sebelum_${f%%(*}.sql" "$D/rb_${f%%(*}.sql" >/dev/null && echo "PASS rollback memulihkan ${f%%(*} identik" || echo "INFO rollback ${f%%(*} TIDAK identik dgn sebelum (lihat ROLLBACK)"
done
