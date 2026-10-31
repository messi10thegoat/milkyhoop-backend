#!/usr/bin/env bash
# Uji migrasi V466 di Postgres SEKALI-PAKAI (image sama dgn produksi; skema document_attachments + schema_migrations diambil pg_dump -s
# BACA SAJA dari produksi). Urutan WAJIB: (1) sebelum V466 baris goods_receipt DITOLAK; (2) V466 + catatan schema_migrations dalam SATU
# transaksi = OK, CHECK 27 nilai; (3) baris goods_receipt/vendor_deposit DITERIMA; (4) ROLLBACK GAGAL KERAS saat baris itu ada; (5) baris dihapus
# -> ROLLBACK OK, CHECK kembali 25 nilai, catatan V466 hilang, baris goods_receipt DITOLAK lagi. Keluar 0 hanya bila SEMUA sesuai.
W=${W:-/root/mh-lampf2b}; M=$W/backend/migrations; IMG=$(docker inspect milkyhoop-dev-postgres-1 --format "{{.Config.Image}}")
C=mh-uji-v466-$$; gagal=0
cek() { if [ "$2" = "$3" ]; then echo "OK   $1"; else echo "GAGAL $1 (harap=$3 dapat=$2)"; gagal=1; fi; }
docker run -d --rm --name $C -e POSTGRES_PASSWORD=uji $IMG >/dev/null || exit 9
trap 'docker rm -f $C >/dev/null 2>&1' EXIT
for i in $(seq 1 30); do docker exec $C pg_isready -U postgres >/dev/null 2>&1 && break; sleep 1; done; sleep 2
Q() { docker exec -i $C psql -U postgres -At -v ON_ERROR_STOP=1 "$@"; }
docker exec milkyhoop-dev-postgres-1 sh -c 'PGPASSWORD=$POSTGRES_PASSWORD pg_dump -U postgres -d milkydb -s -t document_attachments -t schema_migrations --no-owner --no-privileges' \
  | docker exec -i $C psql -U postgres -q >/dev/null 2>&1
cek "skema document_attachments + chk_da_entity 25 nilai tersalin" "$(echo "SELECT count(*) FROM pg_constraint WHERE conname='chk_da_entity';" | Q)" "1"
SISIP="INSERT INTO document_attachments (tenant_id, document_id, entity_type, entity_id) VALUES ('uji', gen_random_uuid(), 'goods_receipt', gen_random_uuid());"
r=$(echo "$SISIP" | Q 2>&1 | grep -c chk_da_entity); cek "(1) sebelum V466: goods_receipt DITOLAK chk_da_entity" "$r" "1"
CS=$(sha256sum $M/V466__chk_da_entity_grn_ump.sql | cut -c1-64)
{ cat $M/V466__chk_da_entity_grn_ump.sql; echo "INSERT INTO schema_migrations(version, checksum, status, applied_by) VALUES ('V466__chk_da_entity_grn_ump.sql', '$CS', 'applied', 'BACKEND2');"; } \
  | Q --single-transaction >/dev/null 2>&1; cek "(2) V466 + catatan satu transaksi rc" "$?" "0"
n=$(echo "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname='chk_da_entity';" | Q | grep -o "'[a-z_]*'" | wc -l); cek "(2) CHECK kini 27 nilai" "$n" "27"
cek "(2) schema_migrations mencatat V466 + checksum berkas" "$(echo "SELECT checksum FROM schema_migrations WHERE version='V466__chk_da_entity_grn_ump.sql';" | Q)" "$CS"
echo "$SISIP" | Q >/dev/null 2>&1; cek "(3) goods_receipt DITERIMA" "$?" "0"
echo "INSERT INTO document_attachments (tenant_id, document_id, entity_type, entity_id) VALUES ('uji', gen_random_uuid(), 'vendor_deposit', gen_random_uuid());" | Q >/dev/null 2>&1
cek "(3) vendor_deposit DITERIMA" "$?" "0"
r=$(Q --single-transaction < $M/V466__chk_da_entity_grn_ump_ROLLBACK.sql 2>&1 | grep -c "V466 rollback: masih ada lampiran"); cek "(4) ROLLBACK GAGAL KERAS saat baris ada" "$r" "1"
n=$(echo "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname='chk_da_entity';" | Q | grep -o "'[a-z_]*'" | wc -l); cek "(4) CHECK tetap 27 (rollback gagal = nol berubah)" "$n" "27"
echo "DELETE FROM document_attachments WHERE entity_type IN ('goods_receipt','vendor_deposit');" | Q >/dev/null
Q --single-transaction < $M/V466__chk_da_entity_grn_ump_ROLLBACK.sql >/dev/null 2>&1; cek "(5) ROLLBACK OK sesudah baris dihapus" "$?" "0"
n=$(echo "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname='chk_da_entity';" | Q | grep -o "'[a-z_]*'" | wc -l); cek "(5) CHECK kembali 25 nilai" "$n" "25"
cek "(5) catatan V466 dihapus dari schema_migrations" "$(echo "SELECT count(*) FROM schema_migrations WHERE version LIKE 'V466%';" | Q)" "0"
r=$(echo "$SISIP" | Q 2>&1 | grep -c chk_da_entity); cek "(5) goods_receipt DITOLAK lagi" "$r" "1"
echo "HASIL_MIGRASI $([ $gagal = 0 ] && echo LULUS || echo GAGAL)"; exit $gagal
