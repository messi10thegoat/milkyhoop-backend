# Konfig gerbang unit BUG-216 (alamat kirim PO opsional + bawaan snapshot; warehouse_id HANYA bila gudang dipilih; GRN tak berubah). Tiap cabang penentu disabotase; tiap sabotase WAJIB membuat tes merah.
W=/root/mh-b216; CABANG=feat/po-alamat-kirim-bawaan; NAMA_JENDELA=alamat216
POLA_UNIT='purchase_order|po_|grn|goods_receipt|gudang|izin_rute|inventaris_rute|pagar_rute|permission'
HARNESS=scripts/ops/uji-salinan-alamat216.sh
PENANDA_BERKAS=services/po_alamat_kirim.py; PENANDA='gid_simpan'
A=backend/api_gateway/app; T=tests/unit/test_po_alamat_216.py; PAKF=$A/services/po_alamat_kirim.py; PRF=$A/services/po_rencana.py; POF=$A/routers/purchase_orders.py
SAB=(
 "a1 alamat manual tak menang|$PAKF|s#    if manual:#    if False:#|$T"
 "a2 gudang dipilih diabaikan (selalu utama)|$PAKF|s#asal = pilih if gudang_dipilih else await gudang_utama(conn, tid)#asal = await gudang_utama(conn, tid)#|$T"
 "a3 gudang tanpa alamat tak jatuh ke alamat usaha|$PAKF|s#    if alamat is None:#    if False:#|$T"
 "a4 alamat usaha diabaikan|$PAKF|s#alamat = await alamat_usaha(conn, tid)#alamat = None#|$T"
 "a5 bawaan gudang UTAMA disimpan ke kolom (GRN ikut berubah)|$PAKF|s#gid_simpan = pilih\[\"id\"\] if pilih else None#gid_simpan = (pilih or await gudang_utama(conn, tid) or {}).get(\"id\")#|$T"
 "a6 gudang yang DIPILIH tak disimpan|$PAKF|s#gid_simpan = pilih\[\"id\"\] if pilih else None#gid_simpan = None#|$T"
 "a7 gudang nonaktif lolos|$PAKF|s#elif periksa_gudang and not w\[\"is_active\"\]:#elif False:#|$T"
 "a8 gudang tak ditemukan lolos|$PAKF|s#            if w is None:#            if False:#|$T"
 "a9 sumber tersimpan selalu manual|$PAKF|s#if w is not None and teks_gudang(w) == s:#if False:#|$T"
 "a10 ubah gudang di non-Draf lolos|$PRF|s#if berubah_gudang and po\[\"status\"\] != \"draft\":#if False:#|$T"
 "a11 ganti gudang tanpa ikut alamat baru|$PRF|s#if \"ship_to_address\" in data or berubah_gudang:#if \"ship_to_address\" in data:#|$T"
 "a12 buat tak menyimpan gudang yang dipilih|$POF|s#_alamat\[\"warehouse_id\"\],  \# BUG-216#None,  \# BUG-216#|$T"
 "a13 buat mengabaikan alamat bawaan|$POF|s#^\( *\)_alamat\[\"ship_to_address\"\],#\1body.ship_to_address,#|$T"
 "a14 PATCH tak memakai data efektif|$POF|s#update_data = _rc\[\"data_efektif\"\]#update_data = body.model_dump(exclude_unset=True)#|$T"
 "a15 blank spasi dianggap alamat|$PAKF|s#manual = (alamat_manual or \"\").strip()#manual = alamat_manual#|$T"
)
