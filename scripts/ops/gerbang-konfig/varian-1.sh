# Konfig gerbang terfokus B1 varian (BUG-159; dibaca scripts/ops/gerbang-unit.sh). Tanpa migrasi.
# Pemisah sabotase = "|", ekspresi sed memakai "@".
W=/root/mh-varian; CABANG=feat/varian-matriks-b1; NAMA_JENDELA=varian
POLA_UNIT='barang_varian|matrix_items|matrix-items|permission_middleware|inventaris_rute|pagar_rute_izin|cari_barang|izin_rute|tulis_barang_baru|create_item'
HARNESS=scripts/ops/uji-salinan-varian.sh
PENANDA_BERKAS=services/barang_varian.py; PENANDA='def rakit('
A=backend/api_gateway/app
S=$A/services/barang_varian.py
T=tests/unit/test_barang_varian_b1.py
SAB=(
 "s1 batas varian dilonggarkan (1000)|$S|s@MAKS_DIMENSI, MAKS_NILAI, MAKS_ANAK, MAKS_NAMA = 2, 20, 100, 100@MAKS_DIMENSI, MAKS_NILAI, MAKS_ANAK, MAKS_NAMA = 2, 20, 1000, 100@|$T"
 "s2 nilai kembar tak ditolak|$S|s@if len({v.lower() for v in nilai}) != len(nilai):@if False:@|$T"
 "s3 induk ikut berstok|$S|s@body.name, False, user_id@body.name, True, user_id@|$T"
 "s4 penandaan induk tanpa saring tenant|$S|s@SET is_matrix_parent = true WHERE id = \$1 AND tenant_id = \$2@SET is_matrix_parent = true WHERE id = \$1@|$T"
 "s5 penandaan anak tanpa saring tenant|$S|s@WHERE id = \$1 AND tenant_id = \$4@WHERE id = \$1@|$T"
 "s6 tanpa kunci serentak per tenant|$S|s@await _kunci_tenant(conn, ctx\[\"tenant_id\"\])@pass@|$T"
 "s7 bentrok nama tak menolak penulisan|$S|s@    if r\[\"blocks\"\]:@    if False:@|$T"
 "s8 dimensi tambah tak dicocokkan|$S|s@if ada and ada != baru:@if False:@|$T"
 "s9 router tak dipasang di main|$A/main.py|s@app.include_router(matrix_items.router,@app.include_router(matrix_items.router_x,@|$T"
 "s10 izin buat varian dicabut|$A/middleware/permission_middleware.py|s@(r\"^/api/matrix-items(/preview)?\$\", \[\"POST\"\], \"item\", \"C\"),@@|$T"
)
