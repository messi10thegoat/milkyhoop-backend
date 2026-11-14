# Konfig gerbang terfokus Satuan S-1 + bawaan_berlaku (14 Nov 2026): penentu pemakai satuan, /units/summary, ?aksi=, GET /units/{id}, bawaan_berlaku (akun/pajak + sumber). Tanpa migrasi.
# Pemisah sabotase = "|", ekspresi sed memakai "@".
W=/root/mh-satuan-s1; CABANG=feat/satuan-s1; NAMA_JENDELA=satuan-s1
POLA_UNIT='satuan_pakai|satuan_master|bawaan_berlaku|routers.units|routers.items|test_onboarding_bawaan|satuan_dasar'
HARNESS=scripts/ops/uji-salinan-satuan-s1.sh
PENANDA_BERKAS=services/satuan_pakai.py; PENANDA='def tetapkan_pemakai('
A=backend/api_gateway/app
T=tests/unit/test_satuan_pakai.py
B=tests/unit/test_bawaan_berlaku.py
SAB=(
 "s1 kunci mirip tak membuang titik|$A/services/satuan_pakai.py|s@return _kunci(teks).strip(\" .,\")@return _kunci(teks)@|$T"
 "s2 konversi tak dihitung|$A/services/satuan_pakai.py|s@(\"jumlah_konversi\", konv)@(\"jumlah_konversi\", \[\])@|$T"
 "s3 terpakai hanya barang|$A/services/satuan_pakai.py|s@\"terpakai\": bool(p\[\"jumlah_barang\"\] or p\[\"jumlah_konversi\"\])@\"terpakai\": bool(p[\"jumlah_barang\"])@|$T"
 "s4 satuan terpakai boleh nonaktif|$A/services/satuan_pakai.py|s@    elif pakai:@    elif False:@|$T"
 "s5 satuan bawaan boleh nonaktif|$A/services/satuan_pakai.py|s@    kunci_sistem = u.get(\"is_system\")@    kunci_sistem = False@|$T"
 "s6 duplikat tak terdeteksi|$A/services/satuan_pakai.py|s@if len(ids) > 1@if len(ids) > 99@|$T"
 "s7 ringkasan beda dari filter|$A/services/satuan_pakai.py|s@return {k: len(saring(baris, k)) for k in SARING}@return {k: len(baris) for k in SARING}@|$T"
 "s8 filter ngawur diam|$A/services/satuan_pakai.py|s@    raise ValueError(aksi)@    return list(baris)@|$T"
 "s9 aksi_semua kosong|$A/services/satuan_pakai.py|s@\"aksi_semua\": list(AKSI)@\"aksi_semua\": \[\]@|$T"
 "s10 rute summary berganti nama|$A/routers/units.py|s@\"/summary\"@\"/summary_x\"@|$T"
 "s11 hapus tak memakai penentu|$A/routers/units.py|s@pakai = (await SP.muat_pemakai(@pakai = (await SP.muat_x(@|$T"
 "s12 kolom sama bawaan usaha jadi barang|$A/services/bawaan_berlaku.py|s@if nilai is None or (bu and nilai == bu\[\"id\"\]):@if nilai is None:@|$B"
 "s13 pembelian berstok bukan persediaan|$A/services/bawaan_berlaku.py|s@        return usaha.get(\"akun_persediaan\")@        return usaha.get(\"akun_hpp\")@|$B"
 "s14 pembelian jasa ikut HPP|$A/services/bawaan_berlaku.py|s@        return usaha.get(\"akun_beban_jasa\")@        return usaha.get(\"akun_hpp\")@|$B"
 "s15 teks tanpa pajak dikosongkan|$A/services/bawaan_berlaku.py|s@TEKS_PAJAK = \"Tanpa pajak\"@TEKS_PAJAK = \"\"@|$B"
 "s16 teks butir kosong hilang|$A/services/bawaan_berlaku.py|s@ if hasil\[b\] is None}@ if False}@|$B"
 "s17 detail tanpa bawaan_berlaku|$A/routers/items.py|s@\"bawaan_berlaku\": bawaan_berlaku,@\"bawaan_x\": bawaan_berlaku,@|$B"
)
