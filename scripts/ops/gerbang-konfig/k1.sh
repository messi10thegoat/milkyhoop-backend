# Konfig gerbang unit K-1 (daftar STANDAR SO master Kategori barang: GET /api/items/categories?bentuk=objek + /summary; tanpa migrasi, hanya baca). Tiap sabotase WAJIB membuat tes merah.
W=/root/mh-k1; CABANG=feat/kategori-k1; NAMA_JENDELA=k1
POLA_UNIT='k1_kategori|kategori_daftar|kategori|izin_rute|sql_placeholder'
HARNESS=scripts/ops/uji-salinan-k1.sh
PENANDA_BERKAS=services/kategori_daftar.py; PENANDA='SQL_DAFTAR'
A=backend/api_gateway/app; T=backend/api_gateway/tests/unit/test_k1_kategori_daftar.py; KF=$A/services/kategori_daftar.py; IT=$A/routers/items.py
SAB=(
 "k1 barang terhapus ikut dihitung|$KF|s# AND p.deleted_at IS NULL##g|$T"
 "k2 hitung barang tanpa saring tenant|$KF|s#p.tenant_id = c.tenant_id AND ##g|$T"
 "k3 pencocokan teks peka huruf|$KF|s#lower(p.kategori) = lower(c.name)#p.kategori = c.name#g|$T"
 "k4 jumlah aktif = jumlah barang|$KF|s#FILTER (WHERE p.status = 'active')#FILTER (WHERE true)#|$T"
 "k5 saringan tanpa_barang diabaikan|$KF|s#HAVING (\$3::boolean IS NOT TRUE OR COUNT(p.id) = 0)#HAVING (\$3::boolean IS NOT TRUE OR TRUE)#g|$T"
 "k6 hapus selalu tersedia|$KF|s#aksi(a == 0, alasan_hapus)#aksi(True, alasan_hapus)#|$T"
 "k7 hapus tak tersedia bila ada barang (bukan aktif)|$KF|s#aksi(a == 0, alasan_hapus)#aksi(n == 0, alasan_hapus)#|$T"
 "k8 limit tak dijepit|$KF|s#BATAS_MAKS = 200#BATAS_MAKS = 200000#|$T"
 "k9 aksi tak dikenal diterima|$KF|s#if kunci_aksi and kunci_aksi not in AKSI:#if False:#|$T"
 "k10 urutan peka huruf besar|$KF|s#ORDER BY lower(c.name), c.id#ORDER BY c.name, c.id#|$T"
 "k11 bentuk lama dihapus|$IT|s#return CategoryListResponse(success=True, categories=categories)#return {\"success\": True, \"categories\": []}#|$T"
 "k12 422 tertelan jadi 500|$IT|/async def list_categories/,/Error listing categories/{/    except HTTPException:/d;}|$T"
 "k13 izin rute summary hilang|$A/middleware/permission_middleware.py|s#categories/summary\$\", \[\"GET\"\], \"item\", \"R\"#categories/summary\$\", [\"GET\"], \"item\", \"X\"#|$T"
)
