# Konfig gerbang unit G3 Gudang (pratinjau + penulis satu penentu, kode otomatis, utama nonaktif = blok, ber-stok = peringatan, jenis_text). Tiap sabotase WAJIB membuat tes merah.
W=/root/mh-g3r2; CABANG=feat/gudang-g3-r2; NAMA_JENDELA=g3
POLA_UNIT='gudang|warehouse|g3|izin_rute|sql_placeholder|permission|persediaan_standar|pemindahan|idem|aksi_tersedia'
HARNESS=scripts/ops/uji-salinan-g3.sh
PENANDA_BERKAS=services/gudang_rencana.py; PENANDA='async def rencana_status'
A=backend/api_gateway/app; T=tests/unit/test_gudang_g3.py; GRF=$A/services/gudang_rencana.py; WHF=$A/routers/warehouses.py
SAB=(
 "g1 kode otomatis nomor salah|$GRF|s#default=0) + 1:03d#default=0) + 2:03d#|$T"
 "g2 kode manual ganda lolos|$GRF|s#elif kode and await _kode_dipakai(conn, tid, kode):#elif False:#|$T"
 "g3 awalan SYS- dicadangkan lolos|$GRF|s#if kode and kode.upper().startswith(AWALAN_SISTEM):#if False:#|$T"
 "g4 nama kosong lolos|$GRF|s#^    if not nama:#    if False:#|$T"
 "g5 buat: peringatan utama diganti hilang|$GRF|s#elif utama and (lama := await _utama_lain(conn, tid)):#elif False:#|$T"
 "g6 teks blok utama nonaktif salah|$GRF|s#TEKS_UTAMA_TAK_NONAKTIF = \"Jadikan gudang lain sebagai Utama dulu.\"#TEKS_UTAMA_TAK_NONAKTIF = \"Gudang utama.\"#|$T"
 "g7 status: nonaktif utama tak diblok|$GRF|s#    elif not aktif and w\[\"is_default\"\]:#    elif False:#|$T"
 "g8 status: peringatan ber-stok hilang|$GRF|s#    elif not aktif and (n := await GD.jumlah_berstok(conn, tid, w\[\"id\"\])):#    elif False:#|$T"
 "g9 status: sudah aktif tak diblok|$GRF|s#elif aktif and w\[\"is_active\"\]:#elif False:#|$T"
 "g10 status: sudah nonaktif tak diblok|$GRF|s#elif not aktif and not w\[\"is_active\"\]:#elif False:#|$T"
 "g11 gudang sistem tak diblok|$GRF|s#^    if w.get(\"is_system\"):#    if False:#|$T"
 "g12 kosong tak menghapus (\"\" tersimpan)|$GRF|s#return v.strip() or None if isinstance(v, str) else v#return v.strip() if isinstance(v, str) else v#|$T"
 "g13 null dianggap mengubah|$GRF|s#if k not in b or b\[k\] is None:#if k not in b:#|$T"
 "g14 label perubahan hilang|$GRF|s#p = {\"field\": k, \"label\": LABEL\[k\],#p = {\"field\": k, \"label\": k,#|$T"
 "g15 peringatan tanpa butuh_konfirmasi|$GRF|s#\"butuh_konfirmasi\": True, \"pesan\"#\"butuh_konfirmasi\": False, \"pesan\"#|$T"
 "g16 galat penulis status salah|$GRF|s#raise GalatBerkode(x\[\"status\"\], x\[\"code\"\], x\[\"message\"\], x\[\"field\"\])#raise GalatBerkode(500, x[\"code\"], x[\"message\"], x[\"field\"])#|$T"
 "g17 kalimat catatan cabang berubah|$A/services/gudang_jenis.py|s#Cabang hanya penanda jenis lokasi#Cabang berarti#|$T"
 "g18 set-default penulis tak lewat penentu|$WHF|s#r = await GR.rencana_utama(conn, ctx\[\"tenant_id\"\], w)#r = {\"blocks\": [], \"nilai\": {\"is_default\": True}}#|$T"
 "g19 izin penulis status bukan U|$A/middleware/permission_middleware.py|s#\"warehouse\", \"U\"),  \# G3: penulis aktif/nonaktif#\"warehouse\", \"C\"),  \# G3: penulis aktif/nonaktif#|$T"
 "g20 aksi_tersedia nonaktifkan tak ikut putusan|$A/services/gudang_daftar.py|s#nonaktif = alasan_nonaktif(w\[\"is_active\"\], w\[\"is_default\"\])#nonaktif = alasan_nonaktif(w[\"is_active\"], True)#|$T"
 "g21 idempotensi buat gudang tak terdaftar|$A/services/idem_buat.py|s#, \"warehouses\": \"code\"}#}#|$T"
 "g22 PATCH lama tak menolak gudang ber-stok|$GRF|s#blocks.append(_blok_berstok(n))                \# PATCH#perin.append(_berstok(n))                \# PATCH#|$T"
 "g23 DELETE lama tak menolak gudang ber-stok|$WHF|s#w, False, blok_berstok=True)#w, False)#|$T"
 "g24 kalimat blok ber-stok berubah|$GRF|s#Pindahkan atau sesuaikan stoknya dulu.\"#Gudang berisi stok.\"#|$T"
)
