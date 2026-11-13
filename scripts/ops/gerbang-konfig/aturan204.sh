# Konfig gerbang unit BUG-204 (aturan stok Penyesuaian: jenis→tanda, non-stok, selisih 0, ganda, fisik; harga pokok 0 = isyarat). Tiap sabotase WAJIB membuat tes merah.
W=/root/mh-b204; CABANG=feat/penyesuaian-aturan-stok; NAMA_JENDELA=aturan204
POLA_UNIT='stock_adj|penyesuaian|persediaan|izin_rute|inventaris_rute|pagar_rute|permission'
HARNESS=scripts/ops/uji-salinan-aturan204.sh
PENANDA_BERKAS=services/stok_sesuai.py; PENANDA='PENYESUAIAN_BARANG_GANDA'
A=backend/api_gateway/app; T=tests/unit/test_penyesuaian_204.py; SSF=$A/services/stok_sesuai.py
SAB=(
 "k1 tanda tak dicek|$SSF|s#elif ARAH_JENIS.get(tipe) and ubah != 0 and (ubah > 0) != (ARAH_JENIS\[tipe\] > 0):#elif False:#|$T"
 "k2 arah decrease dibalik|$SSF|s#\"decrease\": -1,#\"decrease\": 1,#|$T"
 "k3 non-stok lolos|$SSF|s#if not barang_bertstok(tipe_brg):#if False:#|$T"
 "k4 selisih 0 lolos|$SSF|s#        if ubah == 0:#        if False:#|$T"
 "k5 barang ganda lolos|$SSF|s#        if pid in terlihat:#        if False:#|$T"
 "k6 fisik tak wajib|$SSF|s#            if item.physical_quantity is None:#            if False:#|$T"
 "k7 fisik negatif lolos|$SSF|s#            if _d(item.physical_quantity) < 0:#            if False:#|$T"
 "k8 harga pokok 0 tanpa isyarat|$SSF|s#if harga == 0 and isyarat is not None:#if False:#|$T"
 "k9 harga pokok 0 jadi blok|$SSF|s#            isyarat.append(f\"Barang {nama} belum punya harga pokok; nilai Rp 0.\")#            bl.galat(\"HARGA_POKOK_NOL\", 422, \"x\")#|$T"
 "k10 rencana_buat tak membawa isyarat|$SSF|s#\"saldo\": \[\], \"isyarat\": isyarat}#\"saldo\": []}#|$T"
 "k11 total_value tak dimuat|$SSF|s#d\[\"total_value\"\] = rp(sum((b\[\"total_value\"\] for b in r\[\"baris_draf\"\]), NOL))#d[\"total_x\"] = rp(sum((b[\"total_value\"] for b in r[\"baris_draf\"]), NOL))#|$T"
 "k12 total_value ikut walau ditolak|$SSF|s#if \"baris_draf\" in r and not r.get(\"blocks\"):#if \"baris_draf\" in r:#|$T"
 "k13 total ubah tanpa items hilang|$SSF|s#d\[\"total_value\"\] = rp(r\[\"sa\"\]\[\"total_value\"\])#pass#|$T"
)
