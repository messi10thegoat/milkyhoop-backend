# Konfig gerbang unit (dibaca scripts/ops/gerbang-unit.sh). Sabotase = bukti unit ini; tiap baris WAJIB membuat tes merah.
W=/root/mh-terbit-r2; CABANG=fix/terbit-pratinjau-1-r2; NAMA_JENDELA=terbit-1
POLA_UNIT='terbit|sj_so|stok_baris|faktur|so_kirim|fulfill|izin_rute|inventaris_rute'
HARNESS=scripts/ops/uji-salinan-terbit-pratinjau.sh
PENANDA_BERKAS=services/faktur_terbit_rencana.py; PENANDA='PENANDA_TERBIT_PRATINJAU = "terbit-pratinjau-1-w20261110"'
A=backend/api_gateway/app; SI=$A/routers/sales_invoices.py; R=$A/services/faktur_terbit_rencana.py; T=tests/unit/test_terbit_pratinjau_stok.py
SAB=(
 "s1 fallback badan dipulihkan|$SI|s#            return _pratinjau_ditolak(_he.detail, _he.status_code)#            pass#|$T"
 "s2 pratinjau terbit tanpa cek stok|$R|s#                if bk:#                if False and bk:#|$T"
 "s3 penulis pakai available_quantity|$SI|s#warehouse_id, product_id, \"quantity\")#warehouse_id, product_id, \"available_quantity\")#|$T"
 "s4 kalimat stok satuan dasar salinan lokal|$A/services/stok_baris.py|s#pesan = tg.stok_kurang(nama, gudang_nama, tersedia, jumlah, unit or qd.get(\"satuan_dasar\"))#pesan = f\"Stok {nama} tidak cukup\"#|$T"
)
