#!/usr/bin/env bash
# Ulang TAHAP 1 c-2 sesudah perbaikan alat: uji scratch V479 + 3 berkas yang merah/ baru + sabotase (dasar_tagihan mengabaikan pembulatan
# -> tes neto WAJIB merah = kontrol merah regresi HPP).
W=${W:-/root/mh-pembulatan-c2}; cd $W && git fetch -q deploy && echo "HEAD $(git rev-parse --short HEAD)"
L=/root/logs/c2-ulang-$(date -u +%Y%m%dT%H%M%S); mkdir -p $L; echo "log: $L"
M=$W/backend/migrations timeout 900 scripts/ops/uji-scratch-v479.sh > $L/scratch.log 2>&1; echo "scratch rc=$?"; grep -E "^(FAIL|RINGKAS)|dasar semai|semai baru|scratch dihapus" $L/scratch.log
T="tests/unit/test_tagihan_grn.py tests/unit/test_tagihan_neto_terposting.py tests/unit/test_pembulatan_pembelian.py"
timeout 600 scripts/jalankan_unit.sh $W -q -p no:cacheprovider $T > $L/unit.log 2>&1; grep -E "^(FAILED|ERROR) |^=+ .* in " $L/unit.log | tail -8
echo "=== sabotase: dasar_tagihan mengabaikan pembulatan (WAJIB merah)"
P=$(mktemp -d /root/.sabc2-XXXX); mkdir -p $P/backend; rsync -a --exclude __pycache__ --exclude .pytest_cache backend/api_gateway $P/backend/; ln -s $W/backend/migrations $P/backend/migrations
F=$P/backend/api_gateway/app/services/tagihan_aksi.py; sed -i 's/ - Decimal(str(pembulatan or 0)), Decimal(str(pajak or 0))/, Decimal(str(pajak or 0))/' $F
grep -c "Decimal(str(pembulatan or 0))" $F | sed 's/^/sisa_penanda=/'
timeout 600 scripts/jalankan_unit.sh $P -q -p no:cacheprovider tests/unit/test_tagihan_neto_terposting.py tests/unit/test_pembulatan_pembelian.py 2>&1 | grep -E "^(FAILED|ERROR) |^=+ .* in " | tail -6
rm -rf $P; echo SELESAI_SEMUA
