# Konfig gerbang unit BUG-246/247/248 (status_text daftar pemindahan; Transit 404 di GET gudang + label server stok_per_gudang; pratinjau Kirim/Terima; galat PB_* aditif). Tiap sabotase WAJIB membuat tes merah.
W=/root/mh-b246; CABANG=fix/pemindahan-bug246-247-248; NAMA_JENDELA=b246
POLA_UNIT='bug246|pemindahan|stock_transfer|gudang|warehouse|izin_rute|sql_placeholder|k9'
HARNESS=scripts/ops/uji-salinan-k8.sh
PENANDA_BERKAS=services/gudang_transit.py; PENANDA='LABEL'
A=backend/api_gateway/app; T=tests/unit/test_bug246_247_248.py; STF=$A/routers/stock_transfers.py; WHF=$A/routers/warehouses.py; D2F=$A/services/pemindahan_dua_langkah.py; GDF=$A/services/gudang_daftar.py
SAB=(
 "s1 daftar tak memakai teks_daftar|$STF|s#teks = await pd2.teks_daftar(conn, ctx\[\"tenant_id\"\], rows)#teks = {}#|$T"
 "s2 teks daftar tak menghitung diterima|$D2F|s#teks_status(r\[\"status\"\], _NOL, ada.get(r\[\"id\"\], _NOL))#teks_status(r[\"status\"], _NOL, _NOL)#|$T"
 "s3 GET detail gudang sistem terbuka|$WHF|s#\(kolom_turunan()} FROM warehouses WHERE id = \$1 AND tenant_id = \$2\) AND NOT is_system#\1#|$T"
 "s4 GET stok/nilai gudang sistem terbuka|$WHF|s# AND NOT is_system\",#\",#|$T"
 "s5 label Transit hilang|$GDF|s#GT.LABEL if r\[\"is_system\"\] else r\[\"nama\"\]#r[\"nama\"]#|$T"
 "s6 penanda sistem selalu false|$GDF|s#\"sistem\": bool(r\[\"is_system\"\])#\"sistem\": False#|$T"
 "s7 pratinjau Kirim tanpa transit_before/after|$D2F|s#ln\[\"transit_before\"\], ln\[\"transit_after\"\] = _d(ada), _d(ada) + _d(ln\[\"quantity\"\])#pass#|$T"
 "s8 transit_after tak menambah jumlah|$D2F|s#_d(ada) + _d(ln\[\"quantity\"\])#_d(ada)#|$T"
 "s9 pratinjau Terima from kosong|$D2F|s#r\[\"dari\"\] = {\"id\": str(transit_id) if transit_id else None, \"name\": GT.NAMA}#r[\"dari\"] = None#|$T"
 "s10 pratinjau Terima to kosong|$D2F|s#r\[\"ke\"\] = r\[\"tujuan\"\]#r[\"ke\"] = None#|$T"
 "s11 galat tanpa code/message tingkat atas|$A/services/galat_berkode.py|s#return {\"detail\": self.detail, \"code\": self.code, \"message\": self.message}#return {\"detail\": self.detail}#|$T"
 "s12 handler galat detail-objek tak merender badan aditif|$A/main.py|s#content=exc.badan(), headers=exc.headers)   \# BUG-248-c#content={\"detail\": exc.detail}, headers=exc.headers)   \# BUG-248-c#|$T"
 "s13 _blok PB_* kembali HTTPException biasa|$A/services/pemindahan_barang.py|s#return GalatDetailObjek(status, kode, pesan)#return HTTPException(status_code=status, detail={\"code\": kode, \"message\": pesan})#|$T"
)
