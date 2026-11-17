# Konfig gerbang unit BUG-255 (pasang daftar harga ke BANYAK pelanggan: POST /api/customers/bulk/price-list[/preview]). Tiap sabotase WAJIB membuat tes merah. Tanpa migrasi.
W=/root/mh-b255; CABANG=feat/bug255-dh-pelanggan-massal; NAMA_JENDELA=b255
POLA_UNIT='bug255|dh_pelanggan|customer|pelanggan|izin_rute|sql_placeholder'
HARNESS=scripts/ops/uji-salinan-b255.sh
PENANDA_BERKAS=services/dh_pelanggan_massal.py; PENANDA='SQL_UBAH'
A=backend/api_gateway/app; T=backend/api_gateway/tests/unit/test_bug255_dh_pelanggan_massal.py; DF=$A/services/dh_pelanggan_massal.py; CF=$A/routers/customers.py
SAB=(
 "d1 UPDATE tanpa saring tenant|$DF|s#WHERE tenant_id = \$1 AND id = ANY#WHERE id = ANY#|$T"
 "d2 yang sudah sama ikut di-UPDATE|$DF|s#AND price_list_id IS DISTINCT FROM \$3 ##|$T"
 "d3 pelanggan dibaca tanpa saring tenant|$DF|s#WHERE c.tenant_id = \$1 AND c.id = ANY#WHERE c.id = ANY#|$T"
 "d4 batas id tak berlaku|$DF|s#elif diminta_n > batas:#elif False:#|$T"
 "d5 batas saringan = batas id|$DF|s#BATAS_SARINGAN = 2000#BATAS_SARINGAN = 500#|$T"
 "d6 daftar nonaktif diterima|$DF|s#elif target_diminta and not target\\[\"is_active\"\\]:#elif False:#|$T"
 "d7 daftar tak ada diterima|$DF|s#if target_diminta and not target:#if False:#|$T"
 "d8 tanpa pelanggan diterima|$DF|s#if diminta_n == 0:#if False:#|$T"
 "d9 aktivitas per pelanggan tak ditulis|$DF|s#INSERT INTO customer_activities#INSERT INTO xx_activities#|$T"
 "d10 audit massal tak ditulis|$DF|s#CUSTOMER_PRICE_LIST_BULK#CUSTOMER_PRICE_LIST_XX#|$T"
 "d11 tulis boleh di luar transaksi|$DF|s#if not conn.is_in_transaction():#if False:#|$T"
 "d12 kelompok lama tak dihitung|$DF|s#g\\[\"jumlah\"\\] += 1#g[\"jumlah\"] += 0#|$T"
 "d13 tulis tanpa validasi ulang di bawah kunci|$CF|s#r, _ = await _urai_dh_massal(conn, ctx\\[\"tenant_id\"\\], body)      \\# validasi ULANG#r, _ = None, None  \\# validasi ULANG#|$T"
 "d14 tulis tanpa idempotensi|$CF|s#\"CUSTOMER_DH_BULK\", \"batch\"#\"CUSTOMER_DH_BULK_X\", \"batch\"#|$T"
 "d15 izin pasang daftar harga massal hilang|$A/middleware/permission_middleware.py|s#bulk/price-list(/preview)?\$\", \\[\"POST\"\\], \"customer\", \"U\"#bulk/price-list(/preview)?\$\", [\"POST\"], \"customer\", \"X\"#|$T"
 "d16 saringan daftar bawaan diabaikan|$CF|s#where += \" AND price_list_id IS NULL\"#where += \"\"#|$T"
 "d17 saringan tanpa batas LIMIT|$CF|s# LIMIT {DHM.BATAS_SARINGAN + 1}\"#\"#|$T"
 "d18 id ganda tak dibuang|$CF|s#list(dict.fromkeys(str(UUID(str(x))) for x in b\\[\"customer_ids\"\\]))#[str(UUID(str(x))) for x in b[\"customer_ids\"]]#|$T"
)
