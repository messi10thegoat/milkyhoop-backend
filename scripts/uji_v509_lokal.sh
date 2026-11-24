#!/usr/bin/env bash
# Uji LOKAL V509 (Stok Opname) di klaster Postgres sementara di Mac -- BUKAN produksi. Skema pendukung = stub minimal tabel yang dirujuk.
# 2 sisi (Law 33): hijau tanpa opname HITUNG, MERAH (P0409) dengan opname HITUNG; pengecualian SA milik opname; indeks parsial; rollback bersih.
set -u
PGBIN=/opt/homebrew/opt/postgresql@16/bin; D=$(mktemp -d); PORT=54999; MIG="$(cd "$(dirname "$0")/../backend/migrations" && pwd)"
export PGPASSWORD=x
$PGBIN/initdb -D $D/d -U u -A trust >/dev/null || exit 2
$PGBIN/pg_ctl -D $D/d -o "-p $PORT -k $D" -l $D/log -w start >/dev/null || exit 2
trap "$PGBIN/pg_ctl -D $D/d -m immediate stop >/dev/null 2>&1; rm -rf $D" EXIT
Q() { $PGBIN/psql -h $D -p $PORT -U u -d postgres -v ON_ERROR_STOP=0 -qtAX -c "$1" 2>&1; }
F() { $PGBIN/psql -h $D -p $PORT -U u -d postgres -v ON_ERROR_STOP=1 -qtAX -f "$1" 2>&1; }
Q "CREATE TABLE warehouses(id uuid PRIMARY KEY DEFAULT gen_random_uuid()); CREATE TABLE stock_adjustments(id uuid PRIMARY KEY DEFAULT gen_random_uuid());
   CREATE TABLE inventory_ledger(id uuid DEFAULT gen_random_uuid(), tenant_id text, warehouse_id uuid, source_type text, source_id uuid, quantity_in numeric DEFAULT 0, quantity_out numeric DEFAULT 0);" >/dev/null
# SABOTASE=trigger|indeks|pengecualian: uji kontrol merah pada SALINAN migrasi (asli tak diubah)
M=$MIG/V509__stok_opname.sql
case "${SABOTASE:-}" in
  trigger) grep -v "CREATE TRIGGER trg_gudang_terkunci" $M > $D/m.sql; M=$D/m.sql;;
  indeks) grep -v "uq_opname_gudang_terkunci ON" $M > $D/m.sql; M=$D/m.sql;;
  pengecualian) sed "s/AND NOT (NEW.source_type = 'STOCK_ADJUSTMENT'[^)]*)//" $M > $D/m.sql; M=$D/m.sql;;
esac
F $M >/dev/null || { echo "GAGAL apply V509"; exit 3; }
W=$(Q "INSERT INTO warehouses DEFAULT VALUES RETURNING id" | head -1); W2=$(Q "INSERT INTO warehouses DEFAULT VALUES RETURNING id" | head -1)
U=00000000-0000-0000-0000-000000000001; SA=00000000-0000-0000-0000-0000000000aa; gagal=0
cek() { if [ "$2" = "$3" ]; then echo "OK   $1"; else echo "GAGAL $1 (harap=$3 dapat=$2)"; gagal=1; fi; }
ins() { Q "INSERT INTO inventory_ledger(tenant_id,warehouse_id,source_type,source_id,quantity_in) VALUES ('t1','$1','$2','$3',1)"; }
cek "1 hijau tanpa opname"                      "$(ins $W SALES_INVOICE $U)" ""
Q "INSERT INTO stock_opnames(tenant_id,opname_number,warehouse_id,opname_date,created_by,status) VALUES ('t1','OPN-2611-0001','$W',now(),'$U','hitung')" >/dev/null
cek "2 MERAH gudang dihitung (jual)"            "$(ins $W SALES_INVOICE $U | grep -c GUDANG_TERKUNCI)" "1"
cek "3 MERAH gudang dihitung (void/pembalik)"   "$(ins $W SALES_INVOICE_VOID $U | grep -c GUDANG_TERKUNCI)" "1"
cek "4 MERAH SA lain (bukan milik opname)"      "$(ins $W STOCK_ADJUSTMENT $SA | grep -c GUDANG_TERKUNCI)" "1"
cek "5 hijau gudang LAIN"                       "$(ins $W2 SALES_INVOICE $U)" ""
cek "6 hijau tenant lain, gudang sama"          "$(Q "INSERT INTO inventory_ledger(tenant_id,warehouse_id,source_type,source_id,quantity_in) VALUES ('t2','$W','SALES_INVOICE','$U',1)")" ""
Q "UPDATE stock_adjustments SET id=id" >/dev/null; Q "INSERT INTO stock_adjustments(id) VALUES ('$SA')" >/dev/null
Q "UPDATE stock_opnames SET adjustment_id='$SA' WHERE opname_number='OPN-2611-0001'" >/dev/null
cek "7 hijau SA MILIK opname tertaut"           "$(ins $W STOCK_ADJUSTMENT $SA)" ""
cek "8 MERAH void SA milik opname masih HITUNG" "$(ins $W STOCK_ADJUSTMENT_VOID $SA | grep -c GUDANG_TERKUNCI)" "1"
cek "9 indeks parsial: 2 opname hitung 1 gudang ditolak" "$(Q "INSERT INTO stock_opnames(tenant_id,opname_number,warehouse_id,opname_date,created_by,status) VALUES ('t1','OPN-2611-0002','$W',now(),'$U','hitung')" | grep -c uq_opname_gudang_terkunci)" "1"
cek "10 hijau opname lain di gudang lain"       "$(Q "INSERT INTO stock_opnames(tenant_id,opname_number,warehouse_id,opname_date,created_by,status) VALUES ('t1','OPN-2611-0003','$W2',now(),'$U','hitung')")" ""
cek "11 status tak sah ditolak"                 "$(Q "UPDATE stock_opnames SET status='xx' WHERE opname_number='OPN-2611-0003'" | grep -c chk_opname_status)" "1"
Q "UPDATE stock_opnames SET status='posted'" >/dev/null
cek "12 hijau lagi setelah kunci lepas (posted)" "$(ins $W SALES_INVOICE $U)" ""
cek "13 nomor urut OPN"                         "$(Q "SELECT generate_stock_opname_number('t1','2026-11-24'::date)")" "OPN-2611-0001"
cek "14 nomor urut naik"                        "$(Q "SELECT generate_stock_opname_number('t1','2026-11-24'::date)")" "OPN-2611-0002"
cek "12b baris hijau benar-benar masuk (5)"      "$(Q "SELECT count(*) FROM inventory_ledger")" "5"
F $MIG/V509__stok_opname_ROLLBACK.sql >/dev/null || { echo "GAGAL rollback"; gagal=1; }
cek "15 rollback bersih (tabel+trigger hilang)" "$(Q "SELECT count(*) FROM pg_class WHERE relname LIKE 'stock_opname%'")|$(Q "SELECT count(*) FROM pg_trigger WHERE tgname='trg_gudang_terkunci'")" "0|0"
cek "16 gerak biasa setelah rollback"           "$(ins $W SALES_INVOICE $U)" ""
cek "16b total baris ledger (6)"                "$(Q "SELECT count(*) FROM inventory_ledger")" "6"
exit $gagal
