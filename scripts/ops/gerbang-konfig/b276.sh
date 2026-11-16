# Konfig gerbang unit BUG-276 (impor ketik: satu kata satuan di antara nama dan medan pertama = satuan dasar, bukan "tak terbaca"). Tiap sabotase WAJIB membuat tes merah. Tanpa migrasi.
W=/root/mh-b276; CABANG=fix/bug276-impor-ketik-satuan-tunggal; NAMA_JENDELA=b276
POLA_UNIT='bug276|bug021|impor_barang|bug101_impor|bug092|izin_rute|sql_placeholder'
HARNESS=scripts/ops/uji-salinan-b276.sh
PENANDA_BERKAS=services/impor_barang.py; PENANDA='satuan_tunggal'
A=backend/api_gateway/app; T=backend/api_gateway/tests/unit/test_bug021_tafsir_sisa.py; IB=$A/services/impor_barang.py
SAB=(
 "q1 satuan tunggal tak pernah dikenali|$IB|s#if satuan is not None and len(sisa) == 1#if False and len(sisa) == 1#|$T"
 "q2 kata medan dianggap satuan|$IB|s# and sisa\\[0\\].lower() not in _KATA_MEDAN_SET##|$T"
 "q3 beberapa kata dianggap satuan|$IB|s#re.fullmatch(_KATA, sisa\\[0\\])#True#|$T"
 "q5 satuan tunggal tak dipasang ke baris|$IB|s#b\\[\"satuan_dasar\"\\] = satuan_tunggal\\[0\\]#pass#|$T"
 "q6 pemanggil tak mengambil satuan|$IB|s#_nama_tanpa_sisa(s\\[:awal\\], galat_nama, satuan_tunggal)#_nama_tanpa_sisa(s[:awal], galat_nama)#|$T"
)
