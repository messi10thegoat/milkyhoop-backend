# Konfig gerbang BUG-286 (18 Nov 2026): saldo batch_warehouse_stock digeser SATU kali (trigger = satu-satunya penggerak) pada faktur beli, faktur jual, void. TANPA migrasi.
# HARNESS = uji-ukur-bug286.sh (TEGAS=1 bawaan: cek gagal -> rc=1) + sidik kartu stok/jurnal dibandingkan manual dgn baseline pra-perbaikan. Pemisah sabotase "|", sed "@".
W=/root/mh-286; CABANG=feat/bug286-ukur-r2; NAMA_JENDELA=b286
POLA_UNIT='bug286|batch_standar|inventory_helpers|sales_invoice|bills|void|fulfil|item_batch|stok_baris'
HARNESS=scripts/ops/uji-ukur-bug286.sh
PENANDA_BERKAS=services/inventory_helpers.py; PENANDA='BUG-286: penandaan habis'
A=backend/api_gateway/app
T=tests/unit/test_bug286_batch_satu_penggerak.py
SAB=(
 "z1 cek batch penjualan tanpa saring tenant|$A/routers/sales_invoices.py|s@WHERE tenant_id = \$3 AND batch_id = \$1 AND warehouse_id = \$2@WHERE batch_id = \$1 AND warehouse_id = \$2@|$T"
 "z2 pembalik menggeser manual lagi (bawaan True)|$A/services/inventory_helpers.py|s@    pindah_batch: bool = False,@    pindah_batch: bool = True,@|$T"
 "z3 penandaan habis hilang di pembalik|$A/services/inventory_helpers.py|s@        if batch_id:   # BUG-286: penandaan habis@        if False:   # BUG-286: penandaan habis@|$T"
 "z4 faktur jual mengurangi batch manual lagi|$A/routers/sales_invoices.py|s@        # Batch: saldo digeser HANYA oleh trigger@        await conn.execute(\"UPDATE batch_warehouse_stock SET quantity = quantity - 1\")  # Batch: saldo digeser HANYA oleh trigger@|$T"
 "z5 faktur beli menggeser batch manual lagi|$A/services/bills_service.py|s@# BUG-286: saldo batch_warehouse_stock digeser HANYA@_x = \"batch_warehouse_stock\"  # BUG-286: saldo@|$T"
)
