#!/usr/bin/env bash
# V493 (chk_da_entity + bank_transaction/bank_transfer/employee_advance) uji di SALINAN: SATU klon milkydb. HAPUS klon (trap).
#  M0 sebelum: jenis baru DITOLAK CHECK (CHECK hidup, uji-bicara)
#  M1 apply: constraint memuat 3 nilai baru; sidik document_attachments tenant uji kaos/nyawit TETAP (data grapgrap tak dibaca)
#  M2 tautan uji bank_transaction (tenant kaos, dokumen + mutasi nyata kaos) -> rollback GAGAL KERAS; constraint tetap V493
#  M3 tautan uji dilepas -> rollback SUKSES; constraint == V466 (27 nilai); jenis baru ditolak lagi
#  M4 apply ulang: sukses; sidik akhir == sidik awal
# Pakai: POHON=/root/mh-v493 scripts/ops/uji-salinan-v493.sh (cek-sibuk dulu).
set -u
P=milkyhoop-dev-postgres-1; S=scratch_v493_$(date +%s); POHON=${POHON:-/root/mh-v493}; M=$POHON/backend/migrations; T=kaos-biru-konveksi
hapus() { docker exec $P psql -U postgres -At -c "DROP DATABASE IF EXISTS $S" >/dev/null
          echo "salinan $S dihapus; sisa DB scratch_v493%: $(docker exec $P psql -U postgres -At -c "SELECT count(*) FROM pg_database WHERE datname LIKE 'scratch_v493%'")"; }
trap hapus EXIT
q() { docker exec -i $P psql -U postgres -d $S -v ON_ERROR_STOP=1 -At "$@"; }
docker exec $P psql -U postgres -At -c "CREATE DATABASE $S" >/dev/null || exit 2
docker exec $P sh -c "pg_dump -U postgres -d milkydb --no-owner --no-acl | psql -q -U postgres -d $S" >/tmp/v493_restore.log 2>&1
echo "salinan: $S restore galat: $(grep -c ERROR /tmp/v493_restore.log)"
lulus=0; gagal=0
cek() { if [ "${2:-}" = "1" ]; then lulus=$((lulus+1)); echo "OK   $1"; else gagal=$((gagal+1)); echo "FAIL $1 -- ${3:-}"; fi; }
SIDIK="SELECT count(*) || ':' || md5(COALESCE(string_agg(x::text, '|' ORDER BY x.id), '')) FROM document_attachments x WHERE x.tenant_id IN ('kaos-biru-konveksi', 'pt-kita-nyawit-bersama')"
KON="SELECT pg_get_constraintdef(oid) FROM pg_catalog.pg_constraint WHERE conname = 'chk_da_entity'"
nilai() { q -c "$KON" | grep -o "'[a-z_]*'" | sort | tr "\n" " "; }
s0=$(q -c "$SIDIK"); k0=$(q -c "$KON"); n0=$(nilai)
DOK=$(q -c "SELECT id FROM documents WHERE tenant_id = '$T' ORDER BY id LIMIT 1")
BT=$(q -c "SELECT id FROM bank_transactions WHERE tenant_id = '$T' ORDER BY id LIMIT 1")
TAUT="INSERT INTO document_attachments (tenant_id, document_id, entity_type, entity_id, attachment_type, display_order) VALUES ('$T', '$DOK', 'bank_transaction', '$BT', 'attachment', 0)"
q -c "$TAUT" >/dev/null 2>&1; cek "M0 sebelum V493 jenis baru ditolak CHECK" $([ $? -ne 0 ] && echo 1) "insert lolos"
cek "M1 berkas: nilai berkutip" $([ "$(grep -c "'sales_invoice'" $M/V493__chk_da_entity_kasbank_kasbon.sql)" = "1" ] && echo 1) "kutip hilang"
q -f - < $M/V493__chk_da_entity_kasbank_kasbon.sql >/dev/null; cek "M1 apply" $([ $? -eq 0 ] && echo 1) "apply gagal"
k1=$(q -c "$KON"); for v in bank_transaction bank_transfer employee_advance; do cek "M1 constraint memuat $v" $(echo "$k1" | grep -q "'$v'" && echo 1) "$k1"; done
cek "M1 nol baris berubah" $([ "$(q -c "$SIDIK")" = "$s0" ] && echo 1) "sidik beda"
q -c "$TAUT" >/dev/null; cek "M2 tautan uji bank_transaction diterima" $([ $? -eq 0 ] && echo 1) "insert ditolak"
q -f - < $M/V493__chk_da_entity_kasbank_kasbon_ROLLBACK.sql >/tmp/v493_rb.log 2>&1; rc=$?
cek "M2 rollback GAGAL KERAS selama dipakai" $([ $rc -ne 0 ] && grep -q "V493 rollback: masih ada lampiran" /tmp/v493_rb.log && echo 1) "rc=$rc $(tail -2 /tmp/v493_rb.log)"
cek "M2 constraint tetap V493 sesudah rollback gagal" $([ "$(q -c "$KON")" = "$k1" ] && echo 1) "berubah"
q -c "DELETE FROM document_attachments WHERE tenant_id = '$T' AND entity_type = 'bank_transaction' AND entity_id = '$BT'" >/dev/null
q -f - < $M/V493__chk_da_entity_kasbank_kasbon_ROLLBACK.sql >/dev/null; cek "M3 rollback sukses tanpa pemakaian" $([ $? -eq 0 ] && echo 1) "rollback gagal"
n3=$(nilai); cek "M3 nilai constraint == sebelum (27 nilai V466)" $([ "$n3" = "$n0" ] && [ "$(echo $n3 | wc -w)" = "27" ] && echo 1) "sebelum=[$n0] sesudah=[$n3]"
[ "$(q -c "$KON")" = "$k0" ] || echo "info: teks pg_get_constraintdef beda (bentuk), nilai dibanding di atas"
q -c "$TAUT" >/dev/null 2>&1; cek "M3 jenis baru ditolak lagi" $([ $? -ne 0 ] && echo 1) "insert lolos"
q -f - < $M/V493__chk_da_entity_kasbank_kasbon.sql >/dev/null; cek "M4 apply ulang" $([ $? -eq 0 ] && echo 1) "apply ulang gagal"
cek "M4 constraint == apply pertama" $([ "$(q -c "$KON")" = "$k1" ] && echo 1) "beda"
cek "M4 sidik akhir == awal (nol baris berubah)" $([ "$(q -c "$SIDIK")" = "$s0" ] && echo 1) "beda"
echo "$lulus/$((lulus+gagal)) lulus"
[ $gagal -eq 0 ]; echo "rc=$?"
