# Konfig gerbang terfokus BF-1e (jenis_tersedia/aksi_tersedia + teks server + dokumen_tertaut + satu bentuk dokumen + perlu_ditinjau; tanpa jurnal, tanpa migrasi).
# Pemisah sabotase = "|", ekspresi sed memakai "@".
W=/root/mh-bf1e; CABANG=feat/bankfeed-bf1e-tersedia; NAMA_JENDELA=bankfeed-1e
POLA_UNIT='bankfeed|mutasi_bank|bank_accounts|kasbank|guc_tenant|jaga_rekonsiliasi|inventaris_rute_tulis|gate_izin|include_router|izin_untuk_jalur|izin_rute_pengguna'
HARNESS=scripts/ops/uji-salinan-bankfeed-1e.sh
PENANDA_BERKAS=services/bankfeed_tersedia.py; PENANDA='def aksi_tersedia('
A=backend/api_gateway/app
T="tests/unit/test_bankfeed_tersedia.py tests/unit/test_bankfeed_teks_server.py tests/unit/test_bankfeed_impor.py tests/unit/test_bankfeed_tebak.py tests/unit/test_bankfeed_tak_di_bank.py tests/unit/test_bankfeed_catat3.py tests/unit/test_bankfeed_catat.py tests/unit/test_bankfeed_aksi.py"
TS=$A/services/bankfeed_tersedia.py; DF=$A/services/bankfeed_daftar.py; DK=$A/services/bankfeed_dokumen.py; TB=$A/services/bankfeed_tebak.py; BAR=$A/routers/bank_accounts.py
SAB=(
 'e1 jenis tak disaring izin C modul tujuan|'"$TS"'|s@and izin\["jenis"\].get(j, True)@and True@|'"$T"
 'e2 jenis keluar/masuk memuat jenis yang tak dikenal penulis catat (JENIS_BELUM diabaikan)|'"$TS"'|s@if j in BA.JENIS_CATAT and j not in BA.JENIS_BELUM@if True@|'"$T"
 'e3 tanpa izin ubah tetap ada jenis|'"$TS"'|s@    if not izin\["ubah"\]:@    if False:@|'"$T"
 'e4 izin dihitung per baris (bukan sekali per halaman)|'"$DF"'|s@^    izin = await TS.izin_pengguna(user, rekening_id) if rows else TS.izin_semua()@    izin = None@|'"$T"
 'e5 lepaskan tersedia untuk hasil catat|'"$TS"'|s@"lepaskan": _tidak(ALASAN_PAKAI_BATALKAN)@"lepaskan": _ya()@|'"$T"
 'e6 batalkan tersedia saat direkonsiliasi|'"$TS"'|s@batal = _ya() if izin\["void"\] and not rekon else@batal = _ya() if izin["void"] else@|'"$T"
 'e7 lepaskan tersedia saat direkonsiliasi|'"$TS"'|s@return {"lepaskan": _tidak(ALASAN_REKON_LEPAS) if rekon else@return {"lepaskan": _ya() if True else@|'"$T"
 'e8 catat tetap ok walau tak ada jenis|'"$TS"'|s@"catat": _ya() if jenis else _tidak(tutup or ALASAN_TAK_ADA_JENIS),@"catat": _ya(),@|'"$T"
 'e9 perlu_ditinjau tanpa filter tenant|'"$DF"'|s@WHERE tenant_id = $1 AND status IN (.untuk_ditinjau., .mungkin_duplikat.)@WHERE status IN ('"'"'untuk_ditinjau'"'"', '"'"'mungkin_duplikat'"'"')@|'"$T"
 'e10 perlu_ditinjau menghitung status dicatat juga|'"$DF"'|s@status IN (.untuk_ditinjau., .mungkin_duplikat.)@status IN ('"'"'untuk_ditinjau'"'"', '"'"'mungkin_duplikat'"'"', '"'"'dicatat'"'"')@|'"$T"
 'e11 dokumen_tertaut memuat tautan yang sudah dilepas|'"$DF"'|s@ AND t.dilepas_pada IS NULL@@|'"$T"
 'e12 bentuk dokumen: id memakai id bank (bukan id dokumen)|'"$DK"'|s@"id": str(dokumen_id) if dokumen_id else None@"id": str(bt_id) if bt_id else None@|'"$T"
 'e13 bentuk dokumen: kunci jenis salah (semua jadi lainnya)|'"$DK"'|s@KUNCI_SUMBER.get(sumber, KUNCI_LAIN)@KUNCI_LAIN@|'"$T"
 'e14 total masuk_tampil dihitung dari keluar|'"$DF"'|s@"masuk_tampil": tg.rp(jumlah\["masuk"\])@"masuk_tampil": tg.rp(jumlah["keluar"])@|'"$T"
 'e15 keyakinan_text hilang|'"$TB"'|s@    h\["keyakinan_text"\] = KEYAKINAN_TEKS.get(t.get("keyakinan"))@    h["keyakinan_text"] = None@|'"$T"
 'e16 dasar_text menampilkan enum mentah|'"$TB"'|s@    h\["dasar_text"\] = DASAR_TEKS.get(t.get("dasar"))@    h["dasar_text"] = t.get("dasar")@|'"$T"
 'e17 perlu_ditinjau tak dibawa di daftar Kas & Bank|'"$BAR"'|s@"perlu_ditinjau": perlu.get(str(row\["id"\]), 0),@"perlu_ditinjau": 0,@|'"$T"
 'e18 tersedia: aksi baris ditinjau tertutup walau izin ubah ada|'"$TS"'|s@        tutup = None if ubah else ALASAN_TAK_IZIN@        tutup = ALASAN_TAK_IZIN@|'"$T"
)
