# Konfig gerbang unit BUG-165 (rekening bayar bawaan Pembayaran Keluar dari server; Biaya tak berubah). Tiap sabotase WAJIB membuat tes merah.
W=/root/mh-bpbawaan; CABANG=feat/bp-rekening-bawaan; NAMA_JENDELA=bp165
POLA_UNIT='bawaan|bill_payment|pembayaran_keluar|izin_rute|inventaris_rute|pagar_rute|permission'
HARNESS=scripts/ops/uji-salinan-bp165.sh
PENANDA_BERKAS=services/rekening_bawaan.py; PENANDA='SQL_TERAKHIR_BAYAR'
A=backend/api_gateway/app; T=tests/unit/test_bug165_bayar_rekening_bawaan.py; RB=$A/services/rekening_bawaan.py; BP=$A/routers/bill_payments.py
SAB=(
 "b1 rekening nonaktif ikut terpilih|$RB|s#WHERE bp.tenant_id = \$1 AND bp.status = 'posted' AND ba.is_active#WHERE bp.tenant_id = \$1 AND bp.status = 'posted'#|$T"
 "b2 VOID ikut dihitung|$RB|s#bp.status = 'posted' AND ba.is_active#bp.status IN ('posted', 'voided') AND ba.is_active#|$T"
 "b3 tanpa saring tenant|$RB|s#WHERE bp.tenant_id = \$1 AND bp.status#WHERE \$1::text IS NOT NULL AND bp.status#|$T"
 "b4 sumber Biaya berubah|$RB|s#    terakhir = await conn.fetchrow(sql_terakhir, tid)#    terakhir = await conn.fetchrow(SQL_TERAKHIR_BAYAR, tid)#|$T"
 "b5 summary tak memuat bawaan|$BP|s#\"paid_through_bawaan\": bawaan,#\"paid_x\": bawaan,#|$T"
)
