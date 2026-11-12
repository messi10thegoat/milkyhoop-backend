# Konfig gerbang unit BUG-183b (FG standar BOM penuh persis Accurate; month-end menguras sisa WIP WO SELESAI ke 5-90200). Tiap sabotase WAJIB membuat tes merah.
W=/root/mh-prod183b; CABANG=feat/produksi-fg-standar; NAMA_JENDELA=prod183b
POLA_UNIT='produksi|production|izin_rute|inventaris_rute|pagar_rute|permission'
HARNESS=scripts/ops/uji-salinan-prod183b.sh
PENANDA_BERKAS=services/produksi_wip_sisa.py; PENANDA='async def sisa_wip_selesai'
A=backend/api_gateway/app; T=tests/unit/test_produksi_fg_standar.py; PS=$A/services/produksi_terap_standar.py; PW=$A/services/produksi_wip_sisa.py; PR=$A/routers/production.py
SAB=(
 "u1 FG kembali pada biaya AKTUAL|$PR|s#PT.nilai_fg_standar(_planned_std, planned,#PT.nilai_fg_standar(Decimal(str(order[\"actual_material_cost\"] or 0)), planned,#|$T"
 "u2 kumulatif tak dikurangi yang sudah dikredit (dobel)|$PS|s#- Decimal(sudah_dikredit or 0)))#- Decimal(0)))#|$T"
 "u3 month-end mengabaikan WIP|$PR|s#wip_sisa = await PW.sisa_wip_selesai(conn, tenant_id, _wip_id, p_end)#wip_sisa = []#|$T"
 "u4 WO belum selesai ikut dikuras|$PS|s#    return planned is not None and Decimal(str(planned)) > 0 and Decimal(str(baik_sd_akhir or 0)) >= Decimal(str(planned))#    return True#|$T"
 "u5 WO sudah dikuras muncul lagi|$PW|s#            saldo\[m.group(1)\] += Decimal(str(r\[\"debit\"\] or 0)) - Decimal(str(r\[\"credit\"\] or 0))#            pass#|$T"
 "u6 WO lama ber-WIP 0 tersentuh|$PW|s#if s.quantize(Q) != 0\]#if True]#|$T"
 "u7 clamp >= 0 dilepas (WO lama)|$PS|s#    return max(0, int(std + sub - Decimal(sudah_dikredit or 0)))#    return int(std + sub - Decimal(sudah_dikredit or 0))#|$T"
 "u8 tanda arah selisih terbalik (BUG-190)|$PW|s#    if n > 0:#    if n < 0:#|tests/unit/test_month_end_arah.py"
)
