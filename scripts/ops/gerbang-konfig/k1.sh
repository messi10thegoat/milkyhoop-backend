# Konfig gerbang unit K-1 + K-2 (master Kategori barang standar SO: daftar ?bentuk=objek + /summary + detail/riwayat; V498; pratinjau+tulis buat/ubah nama/status + audit). Tiap sabotase WAJIB membuat tes merah. URUTAN RILIS: V498 DULU, baru kode.
W=/root/mh-k1; CABANG=feat/kategori-k1; NAMA_JENDELA=k1
POLA_UNIT='k1_kategori|k2_kategori|kategori_daftar|kategori_aksi|kategori|izin_rute|sql_placeholder'
HARNESS=scripts/ops/uji-salinan-k1.sh
PENANDA_BERKAS=services/kategori_daftar.py; PENANDA='SQL_DAFTAR'
A=backend/api_gateway/app; T=backend/api_gateway/tests/unit/test_k1_kategori_daftar.py; T2=backend/api_gateway/tests/unit/test_k2_kategori_aksi.py
KF=$A/services/kategori_daftar.py; IT=$A/routers/items.py; AF=$A/services/kategori_aksi.py; MG=backend/migrations/V498__item_categories_standar_so.sql
SAB=(
 "k1 barang terhapus ikut dihitung|$KF|s# AND p.deleted_at IS NULL##g|$T"
 "k2 hitung barang tanpa saring tenant|$KF|s#p.tenant_id = c.tenant_id AND ##g|$T"
 "k3 pencocokan teks peka huruf|$KF|s#lower(p.kategori) = lower(c.name)#p.kategori = c.name#g|$T"
 "k4 jumlah aktif = jumlah barang|$KF|s#FILTER (WHERE p.status = 'active')#FILTER (WHERE true)#|$T"
 "k5 saringan tanpa_barang diabaikan|$KF|s#HAVING (\$3::text IS DISTINCT FROM 'tanpa_barang' OR COUNT(p.id) = 0)#HAVING (\$3::text IS DISTINCT FROM 'tanpa_barang' OR TRUE)#g|$T"
 "k6 hapus selalu tersedia|$KF|s#aksi(a == 0, alasan_hapus)#aksi(True, alasan_hapus)#|$T"
 "k7 hapus tak tersedia bila ada barang (bukan aktif)|$KF|s#aksi(a == 0, alasan_hapus)#aksi(n == 0, alasan_hapus)#|$T"
 "k8 limit tak dijepit|$KF|s#BATAS_MAKS = 200#BATAS_MAKS = 200000#|$T"
 "k9 aksi tak dikenal diterima|$KF|s#if kunci_aksi and kunci_aksi not in AKSI:#if False:#|$T"
 "k10 urutan peka huruf besar|$KF|s#ORDER BY lower(c.name), c.id#ORDER BY c.name, c.id#|$T"
 "k11 bentuk lama dihapus|$IT|s#return CategoryListResponse(success=True, categories=categories)#return {\"success\": True, \"categories\": []}#|$T"
 "k12 422 tertelan jadi 500|$IT|/async def list_categories/,/Error listing categories/{/    except HTTPException:/d;}|$T"
 "k13 izin rute summary hilang|$A/middleware/permission_middleware.py|s#categories/summary\$\", \[\"GET\"\], \"item\", \"R\"#categories/summary\$\", [\"GET\"], \"item\", \"X\"#|$T"
 "a1 ubah nama tak menyentuh products|$AF|s#UPDATE products SET kategori = \$3#UPDATE products SET kategori_x = \$3#|$T2"
 "a2 registry tak diubah saat ubah nama|$AF|s#UPDATE item_categories SET name = \$3#UPDATE item_categories SET name_x = \$3#|$T2"
 "a3 ubah label barang tanpa saring tenant|$AF|s#WHERE tenant_id = \$1 AND lower(kategori) = lower(\$2)#WHERE lower(kategori) = lower(\$2)#|$T2"
 "a4 barang terhapus-lunak tak ikut berganti label|$AF|s#WHERE tenant_id = \$1 AND lower(kategori) = lower(\$2)\"#WHERE tenant_id = \$1 AND lower(kategori) = lower(\$2) AND deleted_at IS NULL\"#|$T2"
 "a5 peringatan ubah nama hilang|$AF|s#per = \[f\"{jb} barang ikut berganti kategori#per = [] and [f\"{jb} barang ikut berganti kategori#|$T2"
 "a6 kembar nama tak diblok|$AF|s#    if bentrok:#    if False:#|$T2"
 "a7 kategori bawaan boleh dinonaktifkan|$AF|s#if not ke_aktif and k\[\"is_default\"\]:#if False:#|$T2"
 "a8 peringatan nonaktif ber-barang aktif hilang|$AF|s#per = \[f\"{a} barang aktif masih memakai#per = [] and [f\"{a} barang aktif masih memakai#|$T2"
 "a9 nama sama dianggap perubahan|$AF|s#if n == k\[\"name\"\]:#if False:#|$T2"
 "a10 event audit ubah nama = buat|$AF|s#\"ITEM_CATEGORY_RENAMED\"#\"ITEM_CATEGORY_CREATED\"#|$T2"
 "a11 audit nonaktif tak ditulis|$AF|s#await catat_audit(conn, tid, user_id, \"aktif\" if ke_aktif else \"nonaktif\"#pass  \# #|$T2"
 "a12 kolom akun B1 ikut V498|$MG|s#ALTER TABLE item_categories ADD COLUMN IF NOT EXISTS is_active#ALTER TABLE item_categories ADD COLUMN IF NOT EXISTS sales_account_id UUID; ALTER TABLE item_categories ADD COLUMN IF NOT EXISTS is_active#|$T2"
 "a13 pilihan (bentuk lama) memuat kategori nonaktif|$IT|s#WHERE tenant_id = \$1 AND is_active#WHERE tenant_id = \$1#|$T2"
 "a14 audit buat kategori tak ditulis|$IT|s#await KA.catat_audit(conn, tenant_id, ctx\[\"user_id\"\], \"buat\"#pass  \# #|$T2"
)
