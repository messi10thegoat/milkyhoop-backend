#!/bin/bash
# uji-apply-mig.sh [skrip-yang-diuji] -- uji MIGRASI-AMAN apply_mig.sh di kontainer postgres SEKALI-PAKAI (--network none),
# BUKAN milkydb. Butuh slot server (kontainer sekali-pakai). Keluar 0 = semua kasus hijau; 1 = ada merah.
# Sabotase (kontrol merah): jalankan dengan salinan skrip yang dirusak sebagai argumen pertama.
set -uo pipefail
S=${1:-$(dirname "$0")/apply_mig.sh}
IMG=pgvector/pgvector:pg14; C=mh-uji-migaman-$$; T=$(mktemp -d); M=$T/main/backend/migrations; mkdir -p "$M" "$T/bk"
docker run -d --rm --name "$C" --network none -e POSTGRES_PASSWORD="$(openssl rand -hex 12)" "$IMG" > /dev/null || exit 2
trap 'docker rm -f "$C" > /dev/null 2>&1; rm -rf "$T"' EXIT
for _i in $(seq 1 60); do docker exec "$C" pg_isready -U postgres -q 2>/dev/null && docker exec "$C" sh -c 'PGPASSWORD=$POSTGRES_PASSWORD psql -U postgres -Atc "select 1"' >/dev/null 2>&1 && break; sleep 1; done
q() { docker exec -i "$C" sh -c 'PGPASSWORD="$POSTGRES_PASSWORD" exec psql -U postgres -d ujimig -v ON_ERROR_STOP=1 -At "$@"' sh "$@"; }
docker exec "$C" sh -c 'PGPASSWORD=$POSTGRES_PASSWORD psql -U postgres -qc "create database ujimig"' || exit 2
q -qc "CREATE TABLE schema_migrations (version text PRIMARY KEY, checksum text NOT NULL, status text NOT NULL DEFAULT 'applied'
       CHECK (status = ANY (ARRAY['applied','skipped','untracked-external'])), applied_at timestamptz NOT NULL DEFAULT now(),
       applied_by text NOT NULL DEFAULT 'runner'); CREATE TABLE t1 (id int);" || exit 2
docker exec "$C" sh -c 'mkdir -p /app/backend/api_gateway/app && echo "async def penanda_uji" > /app/backend/api_gateway/app/x.py'
# PAGAR: sasaran WAJIB kontainer uji -- tak pernah milkydb / kontainer prod
export MIG_MAIN=$T/main MIG_PG=$C MIG_GW=$C MIG_DB=ujimig MIG_BACKUP_DIR=$T/bk MIG_BY=UJI
[[ "$MIG_PG" == mh-uji-migaman-* && "$MIG_DB" == ujimig ]] || { echo "PAGAR: sasaran bukan kontainer uji"; exit 2; }
gagal=0
jalan() { bash "$S" "$@" > "$T/out" 2>&1; echo $?; }
rec() { q -c "select count(*) from schema_migrations where version='$1'"; }
ada() { q -c "select count(*) from information_schema.tables where table_name='$1'"; }
cek() { if [ "$2" = "$3" ]; then echo "OK   $1"; else echo "MERAH $1 (harap $3, dapat $2)"; sed 's/^/      | /' "$T/out" | tail -4; gagal=1; fi; }

printf 'CREATE TABLE t_ok (id int);\n' > "$M/V9001__ok.sql"
cek "T1 sukses + penanda ada: rc" "$(jalan V9001__ok.sql --require-marker x.py 'async def penanda_uji')" 0
cek "T1 tercatat 1" "$(rec V9001__ok.sql)" 1; cek "T1 tabel dibuat" "$(ada t_ok)" 1
cek "T1 cadangan dibuat" "$(ls "$T/bk" | grep -c '^ujimig-pre-v9001-')" 1
cek "T1 applied_by dari MIG_BY" "$(q -c "select applied_by from schema_migrations where version='V9001__ok.sql'")" UJI
cek "T2 sudah tercatat: rc" "$(jalan V9001__ok.sql)" 5

printf 'CREATE TABLE t_gagal (id int);\nSELECT 1/0;\n' > "$M/V9002__gagal.sql"
cek "T3 gagal sesudah DDL: rc" "$(jalan V9002__gagal.sql)" 7
cek "T3 nol catatan" "$(rec V9002__gagal.sql)" 0; cek "T3 DDL ikut batal" "$(ada t_gagal)" 0

printf 'ALTER TABLE t1 ADD COLUMN c int;\n' > "$M/V9003__kunci.sql"
( q -c "BEGIN; LOCK TABLE t1 IN ACCESS EXCLUSIVE MODE; SELECT pg_sleep(25); COMMIT;" > /dev/null 2>&1 & )
for _i in $(seq 1 20); do [ "$(q -c "select count(*) from pg_locks l join pg_class c on c.oid=l.relation where c.relname='t1' and l.mode='AccessExclusiveLock' and l.granted")" = 1 ] && break; sleep 0.5; done
t0=$(date +%s); r=$(jalan V9003__kunci.sql); dt=$(( $(date +%s) - t0 ))
cek "T4 kunci ditahan: rc" "$r" 7; cek "T4 nol catatan" "$(rec V9003__kunci.sql)" 0
cek "T4 gagal cepat (<15 dtk, bukan menunggu 25 dtk)" "$([ $dt -lt 15 ] && echo cepat || echo "lambat ${dt}s")" cepat
cek "T4 pesan LOCK_TIMEOUT" "$(grep -c '^LOCK_TIMEOUT' "$T/out")" 1
for _i in $(seq 1 40); do [ "$(q -c "select count(*) from pg_locks l join pg_class c on c.oid=l.relation where c.relname='t1' and l.granted and l.mode='AccessExclusiveLock'")" = 0 ] && break; sleep 1; done

printf "CREATE TABLE t_atom (id int);\nINSERT INTO schema_migrations(version, checksum) VALUES ('V9004__atom.sql', 'x');\n" > "$M/V9004__atom.sql"
cek "T5 catatan + migrasi SATU transaksi (bentrok PK): rc" "$(jalan V9004__atom.sql)" 7
cek "T5 DDL batal bersama catatan" "$(ada t_atom)" 0; cek "T5 nol catatan" "$(rec V9004__atom.sql)" 0

printf 'BEGIN;\nCREATE TABLE t_tx (id int);\nCOMMIT;\n' > "$M/V9005__tx.sql"
cek "T6 kendali transaksi ditolak: rc" "$(jalan V9005__tx.sql)" 9; cek "T6 tak dijalankan" "$(ada t_tx)" 0
printf 'CREATE FUNCTION f_uji() RETURNS int LANGUAGE plpgsql AS $$\nBEGIN\n  RETURN 1;\nEND;\n$$;\n' > "$M/V9006__plpgsql.sql"
cek "T6b plpgsql BEGIN/END bukan kendali transaksi: rc" "$(jalan V9006__plpgsql.sql)" 0

printf 'CREATE INDEX CONCURRENTLY i_uji ON t1(id);\n' > "$M/V9007__conc.sql"
cek "T7 CONCURRENTLY ditolak: rc" "$(jalan V9007__conc.sql)" 10; cek "T7 nol catatan" "$(rec V9007__conc.sql)" 0

cek "T8 penanda tak ada: rc" "$(jalan V9001__ok.sql --require-marker x.py 'tak_ada_xyz')" 4
printf 'SELECT 1;\n' > "$M/V9008__x_ROLLBACK.sql"; cek "T9 berkas ROLLBACK ditolak: rc" "$(jalan V9008__x_ROLLBACK.sql)" 3
cek "T9b nama tak sah (kutip) ditolak: rc" "$(jalan "V9'__x.sql")" 3
cek "T9c MIG_BY tak sah ditolak: rc" "$(MIG_BY="a'b" jalan V9009__by.sql)" 3

printf 'SELECT pg_sleep(4);\nCREATE TABLE t_lama (id int);\n' > "$M/V9010__lama.sql"
cek "T10 statement_timeout: rc" "$(MIG_STATEMENT_TIMEOUT=1s jalan V9010__lama.sql)" 7
cek "T10 nol catatan" "$(rec V9010__lama.sql)" 0; cek "T10 pesan STATEMENT_TIMEOUT" "$(grep -c '^STATEMENT_TIMEOUT' "$T/out")" 1

cek "T11 tanpa kata sandi literal di skrip" "$(grep -cE 'PGPASSWORD=[A-Za-z0-9]' "$S")" 0
cek "T12 bawaan sasaran prod tetap (milkydb, kontainer prod, 5s, 15min)" \
    "$(grep -oE 'MIG_DB:-milkydb\}|MIG_PG:-milkyhoop-dev-postgres-1\}|MIG_LOCK_TIMEOUT:-5s\}|MIG_STATEMENT_TIMEOUT:-15min\}' "$S" | wc -l | tr -d ' ')" 4
[ $gagal = 0 ] && echo "HIJAU: semua kasus" || echo "MERAH"
exit $gagal
