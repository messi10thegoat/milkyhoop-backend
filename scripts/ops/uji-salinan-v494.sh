#!/usr/bin/env bash
# V494 Bank Feed uji di SALINAN: SATU dump milkydb -> klon scratch_v494_<ts>; terapkan V494 (psql -1, ON_ERROR_STOP) -> cek struktur ->
# ROLLBACK -> struktur kembali sama dengan sebelum (pg_dump --schema-only tabel tersentuh) -> terapkan ulang (idempoten lewat rollback) ->
# cek penjaga rollback GAGAL KERAS bila sudah berisi. HAPUS klon (trap). rc 0 = lulus.
set -u
P=milkyhoop-dev-postgres-1; TS=$(date +%s); S=scratch_v494_$TS; W=${POHON:-/root/mh-bf1}; M=$W/backend/migrations
hapus() { docker exec $P psql -U postgres -At -c "DROP DATABASE IF EXISTS $S" >/dev/null
          echo "salinan dihapus; sisa DB scratch_v494%: $(docker exec $P psql -U postgres -At -c "SELECT count(*) FROM pg_database WHERE datname LIKE 'scratch_v494%'")"; }
trap hapus EXIT
q() { docker exec -i $P psql -U postgres -d $S -v ON_ERROR_STOP=1 -At "$@"; }
gagal=0; cek() { if [ "$2" = "$3" ]; then echo "OK   $1"; else echo "GAGAL $1: dapat [$2] harap [$3]"; gagal=1; fi; }
docker exec $P sh -c "pg_dump -U postgres -d milkydb --no-owner --no-acl -Fc -f /tmp/v494_$TS.dump" || exit 2
docker exec $P psql -U postgres -At -c "CREATE DATABASE $S" >/dev/null || exit 2
docker exec $P pg_restore -U postgres -d $S --no-owner --no-acl /tmp/v494_$TS.dump >/tmp/v494_restore.log 2>&1
echo "restore galat: $(grep -c ERROR /tmp/v494_restore.log)"; docker exec $P rm -f /tmp/v494_$TS.dump
skema() { docker exec $P pg_dump -U postgres -d $S --schema-only -t bank_reconciliations -t document_attachments | grep -v '^--' | grep -v '^$' \
           | python3 "$(dirname "$0")/normal-skema-v494.py" > /tmp/v494_skema_$1.sql; md5sum < /tmp/v494_skema_$1.sql | cut -c1-12; }
SEBELUM=$(skema sebelum)
q -1 < $M/V494__bankfeed_mutasi_bank.sql >/tmp/v494_a.log 2>&1; cek "terapkan V494" "$?" "0"
cek "4 tabel baru" "$(q -c "SELECT count(*) FROM pg_tables WHERE tablename IN ('mutasi_bank_impor','mutasi_bank','mutasi_bank_tautan','aturan_bank')")" "4"
cek "RLS+FORCE 4 tabel" "$(q -c "SELECT count(*) FROM pg_class WHERE relname IN ('mutasi_bank_impor','mutasi_bank','mutasi_bank_tautan','aturan_bank') AND relrowsecurity AND relforcerowsecurity")" "4"
cek "4 policy tenant" "$(q -c "SELECT count(*) FROM pg_policy WHERE polname IN ('rls_mutasi_bank_impor','rls_mutasi_bank','rls_mutasi_bank_tautan','rls_aturan_bank')")" "4"
cek "kolom uang numeric(18,2)" "$(q -c "SELECT count(*) FROM information_schema.columns WHERE table_name='bank_reconciliations' AND column_name IN ('difference','reconciled_deposits','reconciled_withdrawals','unreconciled_deposits','unreconciled_withdrawals') AND data_type='numeric' AND numeric_precision=18 AND numeric_scale=2")" "5"
cek "kolom batal" "$(q -c "SELECT count(*) FROM information_schema.columns WHERE table_name='bank_reconciliations' AND column_name IN ('dibatalkan_oleh','dibatalkan_pada','alasan_batal')")" "3"
cek "chk_da_entity +mutasi_bank_impor (31 nilai)" "$(q -c "SELECT array_length(regexp_split_to_array(pg_get_constraintdef(oid), ','), 1) FROM pg_constraint WHERE conname='chk_da_entity'") $(q -c "SELECT pg_get_constraintdef(oid) LIKE '%mutasi_bank_impor%' FROM pg_constraint WHERE conname='chk_da_entity'")" "31 t"
cek "sidik unik per rekening" "$(q -c "SELECT count(*) FROM pg_indexes WHERE tablename='mutasi_bank' AND indexdef LIKE '%UNIQUE%tenant_id, bank_account_id, sidik%'")" "1"
cek "tautan aktif unik parsial" "$(q -c "SELECT count(*) FROM pg_indexes WHERE indexname='ux_mutasi_bank_tautan_aktif' AND indexdef LIKE '%WHERE (dilepas_pada IS NULL)%'")" "1"
cek "baris lama tetap (lampiran)" "$(q -c "SELECT count(*) FROM document_attachments")" "$(docker exec $P psql -U postgres -d milkydb -At -c "SELECT count(*) FROM document_attachments")"
q -1 < $M/V494__bankfeed_mutasi_bank_ROLLBACK.sql >/tmp/v494_r.log 2>&1; cek "rollback kosong" "$?" "0"
cek "tabel hilang" "$(q -c "SELECT count(*) FROM pg_tables WHERE tablename IN ('mutasi_bank_impor','mutasi_bank','mutasi_bank_tautan','aturan_bank')")" "0"
cek "skema tersentuh kembali sama (token restrict dibuang, chk_da_entity = himpunan nilai)" "$(skema sesudah)" "$SEBELUM"; diff /tmp/v494_skema_sebelum.sql /tmp/v494_skema_sesudah.sql | head -10
q -1 < $M/V494__bankfeed_mutasi_bank.sql >/tmp/v494_b.log 2>&1; cek "terapkan ulang" "$?" "0"
BA=$(q -c "SELECT id || '|' || tenant_id FROM bank_accounts ORDER BY id LIMIT 1")
q -c "INSERT INTO aturan_bank (tenant_id, nama, syarat, tindakan) VALUES ('${BA#*|}', 'UJI', '[]', '{}')" >/dev/null
q -1 < $M/V494__bankfeed_mutasi_bank_ROLLBACK.sql >/tmp/v494_g.log 2>&1; cek "rollback GAGAL KERAS bila berisi" "$?" "3"
cek "data tetap sesudah rollback ditolak" "$(q -c "SELECT count(*) FROM aturan_bank")" "1"
echo "rc=$gagal"; exit $gagal
