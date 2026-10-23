#!/usr/bin/env bash
# Uji V455 di DB SCRATCH (klon SKEMA milkydb, tanpa data): terapan, default & CHECK, ROLLBACK, kontrol merah (ROLLBACK ditolak bila ada
# tautan tagih_dulu), DB scratch dihapus + dibuktikan hilang. Prod TIDAK ditulis.
set -u
P=milkyhoop-dev-postgres-1; S=scratch_v455_$(date +%s); M=${M:-/root/mh-v455/backend/migrations}
Q() { docker exec -i $P psql -U postgres -d $S -v ON_ERROR_STOP=1 -At "$@"; }
hapus() { docker exec $P psql -U postgres -At -c "DROP DATABASE IF EXISTS $S" >/dev/null;
          echo "scratch dihapus; sisa DB scratch_v455: $(docker exec $P psql -U postgres -At -c "SELECT count(*) FROM pg_database WHERE datname LIKE 'scratch_v455%'")"; }
trap hapus EXIT
docker exec $P psql -U postgres -At -c "CREATE DATABASE $S" >/dev/null || exit 2
docker exec $P sh -c "pg_dump -U postgres -d milkydb --schema-only --no-owner --no-acl | psql -q -U postgres -d $S" >/dev/null 2>&1
echo "tabel tautan ada: $(Q -c "SELECT count(*) FROM information_schema.tables WHERE table_name = 'goods_receipt_bill_links'")"
Q -1 < $M/V455__grn_tautan_kind.sql >/dev/null 2>/tmp/v455e && echo "PASS V455 terapan" || { echo "FAIL V455: $(tail -2 /tmp/v455e)"; exit 1; }
echo "nilai_tagih: $(Q -c "SELECT data_type||' nullable='||is_nullable FROM information_schema.columns WHERE table_name='goods_receipt_bill_links' AND column_name='nilai_tagih'")"
echo "kolom: $(Q -c "SELECT data_type||' notnull='||(is_nullable='NO')||' default='||column_default FROM information_schema.columns WHERE table_name='goods_receipt_bill_links' AND column_name='kind'")"
# baris uji minimal (FK dilewati di sesi ini; scratch saja)
ISI="SET session_replication_role = replica; INSERT INTO goods_receipt_bill_links (goods_receipt_item_id, bill_item_id, quantity, grni_amount) VALUES (gen_random_uuid(), gen_random_uuid(), 1, 1) RETURNING kind"
echo "bawaan baris baru: $(Q -c "$ISI" | tail -1)"
if Q -c "SET session_replication_role = replica; INSERT INTO goods_receipt_bill_links (goods_receipt_item_id, bill_item_id, quantity, grni_amount, kind) VALUES (gen_random_uuid(), gen_random_uuid(), 1, 1, 'ngawur')" >/dev/null 2>&1; then
  echo "FAIL CHECK menerima 'ngawur'"; else echo "PASS CHECK menolak kind asing"; fi
Q -c "SET session_replication_role = replica; INSERT INTO goods_receipt_bill_links (goods_receipt_item_id, bill_item_id, quantity, grni_amount, kind) VALUES (gen_random_uuid(), gen_random_uuid(), 1, 1, 'tagih_dulu')" >/dev/null
if Q -1 < $M/V455__grn_tautan_kind_ROLLBACK.sql >/dev/null 2>&1; then echo "FAIL MERAH: ROLLBACK lolos padahal ada tautan tagih_dulu"; else echo "PASS MERAH: ROLLBACK ditolak (ada tagih_dulu)"; fi
Q -c "DELETE FROM goods_receipt_bill_links WHERE kind <> 'terima_dulu'" >/dev/null
Q -1 < $M/V455__grn_tautan_kind_ROLLBACK.sql >/dev/null && echo "PASS ROLLBACK (tanpa tautan berjenis lain)"
echo "kolom sesudah rollback: $(Q -c "SELECT count(*) FROM information_schema.columns WHERE table_name='goods_receipt_bill_links' AND column_name='kind'")"
