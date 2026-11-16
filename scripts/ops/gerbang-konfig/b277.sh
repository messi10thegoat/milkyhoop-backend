# Konfig gerbang unit BUG-277 (Ekspor Barang xlsx/csv = kolom templat Impor Barang). Tiap sabotase WAJIB membuat tes merah. Tanpa migrasi.
W=/root/mh-b277; CABANG=feat/bug277-ekspor-barang; NAMA_JENDELA=b277
POLA_UNIT='bug277|ekspor_barang|impor_barang|bug101_impor|bug021|izin_rute|sql_placeholder'
HARNESS=scripts/ops/uji-salinan-b277.sh
PENANDA_BERKAS=services/ekspor_barang.py; PENANDA='SQL_BARANG'
A=backend/api_gateway/app; T=backend/api_gateway/tests/unit/test_bug277_ekspor_barang.py; EBF=$A/services/ekspor_barang.py; IT=$A/routers/items.py
SAB=(
 "e1 awalan rumus tak diberi apostrof|$EBF|s#return \"'\" + v#return v#|$T"
 "e2 tanda = tak dianggap rumus|$EBF|s#_AWAL_RUMUS = (\"=\", #_AWAL_RUMUS = (#|$T"
 "e3 kueri tanpa saring tenant|$EBF|s#WHERE p.tenant_id = \$1 AND p.deleted_at#WHERE p.deleted_at#|$T"
 "e4 barang terhapus ikut|$EBF|s# AND p.deleted_at IS NULL##|$T"
 "e5 konversi nonaktif ikut|$EBF|s# AND uc.is_active = true##|$T"
 "e6 konversi tanpa saring tenant|$EBF|s#uc.tenant_id = p.tenant_id AND ##|$T"
 "e7 tipe non stok salah teks|$EBF|s#\"non_inventory\": \"non stok\"#\"non_inventory\": \"nonstok2\"#|$T"
 "e8 csv tanpa BOM|$EBF|s#\".ufeff\" + ##|$T"
 "e9 judul bukan dari templat impor|$EBF|s#return \[j for _, j, _ in IB.KOLOM\]#return [\"Code\"]#|$T"
 "e10 filter status selalu aktif|$IT|s#params.append(\"active\" if status == \"aktif\" else \"inactive\")#params.append(\"active\")#|$T"
 "e11 filter kategori hilang|$IT|s#sql += f\" AND p.kategori = \${len(params)}\"#sql += \"\"#|$T"
 "e12 tanpa LIMIT|$IT|s#LIMIT {EB.MAKS_BARIS + 1}#LIMIT 99999999#|$T"
 "e13 melebihi batas diterima|$IT|s#if len(rows) > EB.MAKS_BARIS:#if False:#|$T"
 "e14 format tak sah diterima|$IT|s#if format not in (\"xlsx\", \"csv\") or#if#|$T"
 "e15 konversi dilewati tak dihitung|$EBF|s#max(0, len(konversi) - 1)#0#|$T"
 "e16 stok awal diisi nilai|$EBF|s#\"stok_awal\": None#\"stok_awal\": 1#|$T"
)
