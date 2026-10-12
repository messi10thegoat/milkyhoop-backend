#!/bin/bash
# Tautkan journal_id pada pembalik ledger PRODUCTION_OUTPUT_REVERSAL kaos yang NULL (12 Okt 2026, putusan MASTER b).
# DASAR: milkyhoop-inventory Rule 8 carve-out (12 Sep 2026, putusan pemilik): mengisi journal_id NULL -> nilai DIIZINKAN,
# dengan pagar `WHERE journal_id IS NULL` di dalam SQL. journal_id BUKAN fakta gerakan; kuantitas/biaya/saldo TIDAK disentuh.
# Law 1/16 (buku tak berubah: nol baris jurnal disentuh), Law 2 (jurnal POSTED tak diubah), Law 8 (bukan mutasi angka),
# Law 6 (menambah traceability), Law 13 (lock), Law 23 (satu transaksi), Law 24 (tenant eksplisit).
# PEMETAAN diturunkan dari data, bukan dikarang: pembalik ledger -> jurnal PRODUCTION_OUTPUT asli (source_id = WO) ->
# reversed_by_id = jurnal pembalik MANUAL. Syarat tiap baris: tepat 1 jurnal pembalik POSTED milik tenant, |nilai jurnal -
# total_cost ledger| <= Rp20 (WO-31: 1.330.000 vs 1.329.984 = selisih pembulatan lama, ditautkan APA ADANYA, tidak dikoreksi).
#
# PEMAKAIAN (di server BE):
#   tautkan-pembalik-produksi-kaos.sh            -> DRY-RUN: transaksi lalu ROLLBACK, cetak bukti, nol jejak
#   tautkan-pembalik-produksi-kaos.sh --kontrol  -> kontrol MERAH (2 kontrol: pagar IS NULL, toleransi nilai), ROLLBACK
#   tautkan-pembalik-produksi-kaos.sh --jalankan -> COMMIT (HANYA dengan izin LANGSUNG pemilik; ambil pg_dump tabel dulu)
set -uo pipefail
MODE="${1:-dry}"
C=milkyhoop-dev-postgres-1; T=kaos-biru-konveksi
TOL=20; [ "$MODE" = "--kontrol-nilai" ] && TOL=0
BODY=$(cat <<EOF
SELECT pg_advisory_xact_lock(hashtext('PRODUCTION_OUTPUT_REVERSAL_LINK:$T'));
CREATE TEMP TABLE _sebelum AS SELECT * FROM inventory_ledger WHERE tenant_id='$T';
CREATE TEMP TABLE _je_sebelum AS SELECT id, status, total_debit, total_credit, reversed_by_id, content_hash FROM journal_entries WHERE tenant_id='$T';
CREATE TEMP TABLE _ws_sebelum AS SELECT * FROM warehouse_stock WHERE tenant_id='$T';
CREATE TEMP TABLE _peta AS
SELECT il.id AS ledger_id, il.source_number wo, il.total_cost, rv.id AS pembalik_id, rv.journal_number, rv.total_debit,
       count(*) OVER (PARTITION BY il.id) AS n_kandidat
FROM inventory_ledger il
JOIN journal_entries je ON je.tenant_id=il.tenant_id AND je.source_type='PRODUCTION_OUTPUT' AND je.source_id::text=il.source_id::text AND je.status='POSTED' AND je.reversed_by_id IS NOT NULL
JOIN journal_entries rv ON rv.id=je.reversed_by_id AND rv.tenant_id=il.tenant_id AND rv.status='POSTED'
WHERE il.tenant_id='$T' AND il.movement_type='PRODUCTION_OUTPUT_REVERSAL' AND il.journal_id IS NULL;
DO \$\$ DECLARE n int; bad int; BEGIN
  SELECT count(*) INTO n FROM _peta;
  IF n <> 5 THEN RAISE EXCEPTION 'GAGAL: peta berisi % baris, diharapkan 5', n; END IF;
  SELECT count(*) INTO bad FROM _peta WHERE n_kandidat <> 1 OR abs(total_debit - total_cost) > $TOL;
  IF bad <> 0 THEN RAISE EXCEPTION 'GAGAL: % baris tak memenuhi (kandidat tunggal, selisih nilai <= %)', bad, $TOL; END IF;
END \$\$;
\echo == PETA (ledger -> jurnal pembalik)
SELECT wo, journal_number AS jurnal_pembalik, total_cost AS nilai_ledger, total_debit AS nilai_jurnal, total_debit - total_cost AS selisih FROM _peta ORDER BY wo;
CREATE TEMP TABLE _upd AS WITH u AS (
  UPDATE inventory_ledger il SET journal_id = p.pembalik_id
  FROM _peta p WHERE il.id = p.ledger_id AND il.tenant_id='$T' AND il.journal_id IS NULL RETURNING il.id) SELECT id FROM u;
DO \$\$ DECLARE n int; BEGIN
  SELECT count(*) INTO n FROM _upd;
  IF n <> 5 THEN RAISE EXCEPTION 'GAGAL: UPDATE mengenai % baris, diharapkan 5 (pagar journal_id IS NULL)', n; END IF;
END \$\$;
\echo == BUKTI SESUDAH (semua harus true)
SELECT
 (SELECT count(*) FROM inventory_ledger WHERE tenant_id='$T') = (SELECT count(*) FROM _sebelum) AS jumlah_baris_sama,
 (SELECT count(*) FROM inventory_ledger il JOIN _sebelum s USING (id)
   WHERE il.quantity_in IS DISTINCT FROM s.quantity_in OR il.quantity_out IS DISTINCT FROM s.quantity_out OR il.quantity_balance IS DISTINCT FROM s.quantity_balance
      OR il.unit_cost IS DISTINCT FROM s.unit_cost OR il.total_cost IS DISTINCT FROM s.total_cost OR il.average_cost IS DISTINCT FROM s.average_cost
      OR il.warehouse_id IS DISTINCT FROM s.warehouse_id OR il.source_id IS DISTINCT FROM s.source_id OR il.source_type IS DISTINCT FROM s.source_type
      OR il.movement_type IS DISTINCT FROM s.movement_type OR il.movement_date IS DISTINCT FROM s.movement_date OR il.product_id IS DISTINCT FROM s.product_id) = 0 AS angka_gerakan_utuh,
 (SELECT count(*) FROM inventory_ledger il JOIN _sebelum s USING (id) WHERE il.journal_id IS DISTINCT FROM s.journal_id) = 5 AS tepat_5_journal_id_berubah,
 (SELECT count(*) FROM inventory_ledger WHERE tenant_id='$T' AND movement_type='PRODUCTION_OUTPUT_REVERSAL' AND journal_id IS NULL) = 0 AS pembalik_nol_tanpa_jurnal,
 (SELECT count(*) FROM (SELECT id, status, total_debit, total_credit, reversed_by_id, content_hash FROM journal_entries WHERE tenant_id='$T' EXCEPT SELECT * FROM _je_sebelum) x) = 0 AS buku_jurnal_utuh,
 (SELECT count(*) FROM (SELECT * FROM warehouse_stock WHERE tenant_id='$T' EXCEPT SELECT * FROM _ws_sebelum) x) = 0 AS stok_gudang_utuh;
SELECT count(*) AS sisa_wajib_tanpa_jurnal_semua_sumber FROM inventory_ledger il WHERE il.tenant_id='$T'
 AND il.source_type IN ('OPENING_BALANCE','STOCK_ADJUSTMENT','BILL','INVOICE_FULFILLMENT','MATERIAL_ISSUE','PRODUCTION_OUTPUT')
 AND il.id <> '95c594be-4717-4b54-83c6-26bce34d5607'
 AND NOT EXISTS (SELECT 1 FROM journal_entries je WHERE je.tenant_id=il.tenant_id AND je.status='POSTED' AND je.id::text IN (il.journal_id::text, il.source_id::text));
EOF
)
run() { # $1 = akhir transaksi (ROLLBACK|COMMIT), $2 = isi
  printf 'BEGIN;\n%s\n%s;\n' "$2" "$1" | docker exec -i -e PGOPTIONS="${PGO:-}" $C psql -U postgres -d milkydb -v ON_ERROR_STOP=1 -q 2>&1; }
cnt() { docker exec $C psql -U postgres -d milkydb -At -c "SELECT count(*), count(*) FILTER (WHERE journal_id IS NULL) FROM inventory_ledger WHERE tenant_id='$T'"; }
echo "SEBELUM: ledger kaos (jumlah|tanpa journal_id) = $(cnt)"
case "$MODE" in
  dry|--dry) run ROLLBACK "$BODY"; rc=$?;;
  --kontrol)
    echo "== KONTROL 1: pagar WHERE journal_id IS NULL -- UPDATE kedua di transaksi yang sama HARUS menolak (0 baris)"
    run ROLLBACK "$BODY
CREATE TEMP TABLE _upd2 AS WITH u AS (UPDATE inventory_ledger il SET journal_id = p.pembalik_id FROM _peta p WHERE il.id=p.ledger_id AND il.tenant_id='$T' AND il.journal_id IS NULL RETURNING il.id) SELECT id FROM u;
DO \$\$ DECLARE n int; BEGIN SELECT count(*) INTO n FROM _upd2; IF n <> 0 THEN RAISE EXCEPTION 'PAGAR JEBOL: % baris tertimpa', n; END IF; RAISE NOTICE 'pagar IS NULL tegak: UPDATE kedua mengenai 0 baris'; END \$\$;"; rc=$?;;
  --kontrol-nilai)
    echo "== KONTROL 2: toleransi nilai 0 -> WO-31 (selisih Rp16) HARUS ditolak sebelum menulis"
    run ROLLBACK "$BODY"; rc=$?;;
  --jalankan)
    echo "== COMMIT (izin langsung pemilik wajib); cadangan tabel dulu"
    docker exec $C pg_dump -U postgres -d milkydb -t inventory_ledger --data-only -Fc -f /tmp/inventory_ledger_pre_tautkan.dump || { echo GAGAL cadangan; exit 2; }
    docker cp $C:/tmp/inventory_ledger_pre_tautkan.dump /root/backups/inventory_ledger_pre_tautkan_$(date +%Y%m%d%H%M).dump || exit 2
    run COMMIT "$BODY"; rc=$?;;
  *) echo "mode tak dikenal"; exit 64;;
esac
echo "rc=$rc"
echo "SESUDAH:  ledger kaos (jumlah|tanpa journal_id) = $(cnt)"
