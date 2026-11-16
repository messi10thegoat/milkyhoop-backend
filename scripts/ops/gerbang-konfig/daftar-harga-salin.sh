# Konfig gerbang terfokus Daftar Harga SALIN (BUG-256, 16-17 Nov 2026): services/harga_salin.py, POST /{id}/salin/preview + /salin (atomik, idempoten, riwayat 252), izin C + inventaris + rate-limit.
# TANPA migrasi. Cabang turunan puncak rantai (feat/daftarharga-riwayat-252). Pemisah sabotase = "|", sed memakai "@". Tiap sabotase terbukti MERAH lokal (12/12).
W=/root/mh-dh-salin-256; CABANG=feat/daftarharga-salin-256; NAMA_JENDELA=daftar-harga-salin
POLA_UNIT='harga_salin|dh_salin|harga_riwayat|dh_riwayat|bulk_price|bulk_f1|daftar_harga|dh_berjangka|izin_rute'
HARNESS=scripts/ops/uji-salinan-daftar-harga-salin.sh
PENANDA_BERKAS=services/harga_salin.py; PENANDA='async def tulis('
A=backend/api_gateway/app
T=tests/unit/test_dh_salin_256.py
SAB=(
 "q1 salinan jadi bawaan|$A/services/harga_salin.py|s@false, \$5@is_default, \$5@|$T"
 "q2 minimum tak ikut|$A/services/harga_salin.py|s@i.is_default_unit, i.min_price@i.is_default_unit, NULL@|$T"
 "q3 baris tanpa saring tenant|$A/services/harga_salin.py|s@AND pl.tenant_id = \$3@AND \$3 IS NOT NULL@|$T"
 "q4 kode ganda tak dicek|$A/services/harga_salin.py|s@elif await conn.fetchval(\"SELECT 1 FROM price_lists WHERE tenant_id = \$1 AND code = \$2\", tid, kode):@elif False:@|$T"
 "q5 tulis tanpa kunci baris|$A/services/harga_salin.py|s@nama, kunci=True)@nama, kunci=False)@|$T"
 "q6 riwayat dibuang|$A/services/harga_salin.py|s@await HR.catat(conn, ctx, baru, kode, \"BUAT\"@await _lewati(conn, ctx, baru, kode, \"BUAT\"@|$T"
 "q7 kunci idempoten opsional|$A/routers/bulk_price_lists.py|s@SALIN_KUNCI_WAJIB@SALIN_KUNCI_OPSIONAL@|$T"
 "q8 kode ganda jadi 400|$A/services/harga_salin.py|s@(409 if b\[\"code\"\] == \"KODE_DAFTAR_GANDA\" else 400)@400@|$T"
 "q9 peringatan nonaktif dibuang|$A/services/harga_salin.py|s@if ukur\[\"tak_aktif\"\]:@if False:@|$T"
 "q10 izin salin R bukan C|$A/middleware/permission_middleware.py|s@salin(/preview)?\$\", \[\"POST\"\], \"price_list\", \"C\"@salin(/preview)?\$\", [\"POST\"], \"price_list\", \"R\"@|$T"
 "q11 payload pratinjau tanpa nama|$A/routers/bulk_price_lists.py|s@\"payload\": {\"code\": kode, \"name\": nama}@\"payload\": {\"code\": kode}@|$T"
 "q12 kode tak dirapikan|$A/services/harga_salin.py|s@return \" \".join(str(kode or \"\").split()), \" \".join(str(nama or \"\").split())@return str(kode or \"\"), str(nama or \"\")@|$T"
)
