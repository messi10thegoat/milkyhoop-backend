# Konfig gerbang unit BUG-181 (autocomplete barang per kata: semua kata ada, urutan bebas, spasi ganda dirapikan, % _ di-escape; SATU fungsi dgn daftar barang). Tiap sabotase WAJIB membuat tes merah.
W=/root/mh-bom181; CABANG=feat/bom-cari-produk; NAMA_JENDELA=bom181
POLA_UNIT='items|barang|cari_kata|izin_rute|inventaris_rute|pagar_rute|permission'
HARNESS=scripts/ops/uji-salinan-bom181.sh
PENANDA_BERKAS=services/cari_kata.py; PENANDA='def syarat_kata'
A=backend/api_gateway/app; T=tests/unit/test_cari_kata.py; CK=$A/services/cari_kata.py; IT=$A/routers/items.py
SAB=(
 "p1 tanpa escape % _|$CK|s#return kata.replace.*#return kata#|$T"
 "p2 urutan wajib (satu frasa)|$CK|s#    kata = q.split() if isinstance(q, str) else \[\]#    kata = [q.strip()] if isinstance(q, str) and q.strip() else []#|$T"
 "p3 spasi ganda tak dirapikan|$CK|s#q.split() if#q.split(\" \") if#|$T"
 "p4 autocomplete kembali ke kueri lama|$IT|s#cari_sql, cari_params = syarat_kata(q, (\"p.nama_produk\", \"p.item_code\", \"p.sku\"), 3)#cari_sql, cari_params = \"AND p.nama_produk ILIKE \$3\", [f\"%{q}%\"]#|$T"
 "p5 daftar barang tak memakai fungsi bersama|$IT|s#return syarat_kata(search, (\"p.nama_produk\", \"p.barcode\"), idx)#return '', []#|$T"
)
