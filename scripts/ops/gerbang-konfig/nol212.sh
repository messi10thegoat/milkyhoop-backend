# Konfig gerbang unit BUG-212 (posting Penyesuaian nilai 0 = TANPA jurnal, stok tetap bergerak; batal tanpa jurnal = balik stok saja). Tiap sabotase WAJIB membuat tes merah.
W=/root/mh-b212; CABANG=feat/penyesuaian-nilai-nol-tanpa-jurnal; NAMA_JENDELA=nol212
POLA_UNIT='stock_adj|penyesuaian|persediaan|izin_rute|inventaris_rute|pagar_rute|permission|inventory'
HARNESS=scripts/ops/uji-salinan-nol212.sh
PENANDA_BERKAS=services/stok_sesuai.py; PENANDA='TEKS_TANPA_JURNAL_BATAL'
A=backend/api_gateway/app; T=tests/unit/test_persediaan_standar_so.py; SSF=$A/services/stok_sesuai.py; SAF=$A/routers/stock_adjustments.py
SAB=(
 "n1 jurnal selalu ditulis walau nilai 0|$SAF|s#    if r\[\"baris\"\]:#    if True:#|$T"
 "n2 respons posting journal_id 'None'|$SAF|s#\"journal_id\": str(journal_id) if journal_id else None,#\"journal_id\": str(journal_id),#|$T"
 "n3 batal tanpa jurnal tak membalik stok|$SAF|s#                await record_inventory_reversal(#                await (record_inventory_reversal if reversal_journal_id else _tak_ada)(#|$T"
 "n4 gerak cermin batal hanya bila ada jurnal|$SSF|s#, tid, sa_id):#, tid, sa_id) if sa[\"journal_id\"] else []:#|$T"
 "n5 penanda tanpa_jurnal batal salah|$SSF|s#hasil\[\"tanpa_jurnal\"\] = not sa\[\"journal_id\"\]#hasil[\"tanpa_jurnal\"] = False#|$T"
 "n6 penanda tanpa_jurnal posting salah|$SSF|s#hasil\[\"tanpa_jurnal\"\] = not hasil\[\"baris\"\]#hasil[\"tanpa_jurnal\"] = False#|$T"
 "n7 catatan batal memakai teks posting|$SSF|s#TEKS_TANPA_JURNAL_BATAL if \"hapus_draf\" in r else TEKS_TANPA_JURNAL_POSTING#TEKS_TANPA_JURNAL_POSTING#|$T"
 "n8 isyarat harga pokok 0 hilang di posting|$SSF|s#if q != 0 and _d(i\[\"unit_cost\"\]) == 0:#if False:#|$T"
 "n9 pratinjau tak menyatakan tanpa jurnal|$SSF|s#if r.get(\"tanpa_jurnal\") and not r.get(\"blocks\"):#if False:#|$T"
)
