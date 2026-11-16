# Konfig gerbang unit BUG-260 BE + V502 MEREK (stok_fisik_text/bisa_dijual_text per baris + sort_by=bisa_dijual lintas halaman; products.merek: isian POST/PUT, ?merek=, GET /items/merek, duplikat, ekspor, impor). Tiap sabotase WAJIB membuat tes merah.
# URUTAN RILIS: apply V502 DULU (MASTER), baru kode (INSERT/UPDATE barang membaca kolom merek).
W=/root/mh-b260; CABANG=fix/bug260-items-teks-stok; NAMA_JENDELA=b260
POLA_UNIT='bug260|merek|barang_daftar|barang_standar_so|barang_p3|barang_teks|impor|items|izin_rute|sql_placeholder'
HARNESS=scripts/ops/uji-salinan-b260.sh
PENANDA_BERKAS=services/barang_daftar.py; PENANDA='bisa_dijual_text'
A=backend/api_gateway/app; T1=backend/api_gateway/tests/unit/test_barang_bug260.py; T2=backend/api_gateway/tests/unit/test_barang_merek_v502.py
IT=$A/routers/items.py; BDF=$A/services/barang_daftar.py; SCH=$A/schemas/items.py; IMP=$A/services/impor_barang.py; MIG=backend/migrations/V502__products_merek.sql
SAB=(
 "t1 stok_fisik_text tak dikirim per baris|$IT|s#stok_fisik_text=BD.stok_fisik_text(row#stok_fisik_text=BD.tak_ada(row#|$T1"
 "t2 bisa_dijual_text ikut ket (None bila sama stok)|$BDF|s#stok if bisa_dijual is None else bisa_dijual#stok#|$T1"
 "t3 stok_fisik_text tanpa satuan|$BDF|s#return _q(_d(stok)) + (f\" {satuan}\" if satuan else \"\")#return _q(_d(stok))#|$T1"
 "t4 sort bisa_dijual tak lintas halaman (array_position hilang)|$IT|s#sort_col, sort_dir, offset_sql = f\"array_position(\${param_idx}::uuid\[\], p.id)\", \"ASC\", 0#sort_col, sort_dir, offset_sql = \"p.created_at\", \"ASC\", 0#|$T1"
 "t5 has_more/offset SQL tertimpa offset asli|$IT|s#params.extend(\[limit, offset_sql\])#params.extend([limit, offset])#|$T1"
 "t6 sort hanya menghitung potongan halaman|$IT|s#SB.hitung(conn, tenant_id, dil_semua#SB.hitung(conn, tenant_id, dil_semua[:limit]#|$T1"
 "m1 merek dibuang dari INSERT|$IT|s#sales_tax_id, purchase_tax_id, bisa_dikirim, merek,#sales_tax_id, purchase_tax_id, bisa_dikirim,#|$T2"
 "m2 ?merek= diabaikan|$IT|s#        if merek:#        if False and merek:#|$T2"
 "m3 rute /items/merek hilang dari urutan (nama rute berubah)|$IT|s#@router.get(\"/items/merek\")#@router.get(\"/items/merek-x\")#|$T2"
 "m4 ubah barang tak menulis merek|$IT|s#                \"merek\": \"merek\",#                \"merek_x\": \"merek\",#|$T2"
 "m5 duplikat tak menyalin merek|$IT|s#                item_row.get(\"merek\"),#                None,#|$T2"
 "m6 ekspor tanpa kolom Merek|$IT|s#                        \"Merek\",#                        \"Merek-x\",#|$T2"
 "m7 merek tak dipangkas|$SCH|s#        v = (v or \"\").strip()#        v = v#|$T2"
 "m8 impor: sinonim merek bentrok medan lain|$IMP|s#\"merek\": \[\"merek\", \"merk\", \"brand\"\],#\"merek\": [\"merek\", \"merk\", \"brand\", \"kategori\"],#|$T2"
 "m9 migrasi NOT NULL|$MIG|s#ADD COLUMN IF NOT EXISTS merek TEXT;#ADD COLUMN IF NOT EXISTS merek TEXT NOT NULL DEFAULT '';#|$T2"
)
