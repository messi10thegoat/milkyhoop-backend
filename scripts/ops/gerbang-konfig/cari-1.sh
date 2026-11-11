# Konfig gerbang terfokus cari-varian-kode-lock (a: cari_barang varian[] aditif; b: race kode barang otomatis). Tanpa migrasi.
# Pemisah sabotase = "|", ekspresi sed memakai "@".
W=/root/mh-cari2; CABANG=feat/cari-varian-kode-lock; NAMA_JENDELA=cari-varian
POLA_UNIT='cari_barang|tafsir|tulis_barang_baru|galat_bentrok_unik|_siapkan_buat_barang|create_item|barang_baru|nama_barang|kode_barang|saldo_awal|item_sku|impor_barang|search_items'
HARNESS=scripts/ops/uji-salinan-cari.sh
PENANDA_BERKAS=milkybook/v2/cari_barang.py; PENANDA='def varian_lengkap('
A=backend/api_gateway/app
T=tests/unit/test_cari_varian_kode_lock.py
SAB=(
 "s1 varian[] kehilangan atribut|$A/milkybook/v2/cari_barang.py|s@\"atribut\": atr or {}, @@|$T"
 "s2 flag dengan_varian diabaikan|$A/milkybook/v2/cari_barang.py|s@    if dengan_varian:@    if True:@|$T"
 "s3 tool LLM memuat varian lagi|$A/milkybook/v2/tafsir.py|s@, 6, dengan_varian=False)@, 6)@|$T"
 "s4 tanpa kunci per tenant|$A/routers/items.py|s@await conn.execute(\"SELECT pg_advisory_xact_lock(hashtext(\$1))\", f\"ITEM_KODE:{tenant_id}\")@pass@|$T"
 "s5 kode otomatis tak dihitung ulang di bawah kunci|$A/routers/items.py|s@            item_code_to_use = await _kode_otomatis(conn, tenant_id, body.item_type)@            item_code_to_use = item_code_to_use@|$T"
 "s6 bentrok kode otomatis kembali ke pesan lama|$A/routers/items.py|s@\"code\": \"KODE_BARANG_BENTROK\"@\"code\": \"KODE_BARANG_DIPAKAI\"@|$T"
)
