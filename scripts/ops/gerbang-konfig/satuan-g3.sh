# Konfig gerbang terfokus Satuan G3-a aksi tulis (17 Nov 2026): services/satuan_rencana.py (pratinjau + penulis SATU penentu), routers/units.py (POST /preview, /{id}/edit/preview, /{id}/status/preview,
# POST /{id}/status, POST/PATCH/DELETE memakai rencana), satuan_pakai.aksi_tersedia sejalan dengan blok. TANPA migrasi (G3-b = V504 alias). Pemisah sabotase = "|", sed memakai "@".
W=/root/mh-satuan-g3-r3; CABANG=feat/satuan-g3-r3; NAMA_JENDELA=satuan-g3
POLA_UNIT='satuan_g3|satuan_pakai|satuan_master|satuan_tersimpan|satuan_baris|satuan_dasar|routers.units|izin_rute|bawaan_berlaku'
HARNESS=scripts/ops/uji-salinan-satuan-g3.sh
PENANDA_BERKAS=services/satuan_rencana.py; PENANDA='async def rencana_status('
A=backend/api_gateway/app
T=tests/unit/test_satuan_g3.py
SAB=(
 "g1 baris tenant bernama bawaan tak diblok|$A/services/satuan_rencana.py|s@bool(u.get(\"is_system\") or u.get(\"bawaan\"))@bool(u.get(\"is_system\"))@|$T"
 "g2 terpakai boleh diganti nama|$A/services/satuan_rencana.py|s@    if nilai and _bagian(p):@    if False and _bagian(p):@|$T"
 "g3 terpakai boleh dinonaktifkan|$A/services/satuan_rencana.py|s@    elif _bagian(p):@    elif False:@|$T"
 "g4 bentrok singkatan bawaan tak dicek|$A/services/satuan_rencana.py|s@    if SM.kunci(singkatan) in {SM.kunci(x) for x in DEFAULT_UNITS}:@    if False:@|$T"
 "g5 bentrok tenant sensitif huruf|$A/services/satuan_rencana.py|s@lower(abbreviation) = lower(\$2)@abbreviation = \$2@|$T"
 "g6 blok bawaan status 400 bukan 409|$A/services/satuan_rencana.py|s@TEKS_BAWAAN, None, 409)@TEKS_BAWAAN, None, 400)@|$T"
 "g7 pratinjau selalu ok|$A/services/satuan_rencana.py|s@\"ok\": not r\[\"blocks\"\]@\"ok\": True@|$T"
 "g8 penulis tak menolak blok|$A/routers/units.py|s@SR.tolak(r\[\"blocks\"\])@pass@|$T"
 "g9 hapus lama tak memakai penentu|$A/routers/units.py|s@    return await _nonaktifkan(request, unit_id, response, \"Satuan berhasil dihapus\", \"UNIT_DELETE\")@    return {\"success\": True}@|$T"
 "g10 galat 404 bukan berkode|$A/routers/units.py|s@GalatBerkode(404, \"SATUAN_TIDAK_ADA\"@GalatBerkode(404, \"X\"@|$T"
 "g11 gabung dikatakan tersedia|$A/services/satuan_pakai.py|s@a\[\"gabung\"\] = tak(\"Gabung satuan belum tersedia.\")@a[\"gabung\"] = ada()@|$T"
 "g12 ubah_nama terpakai dikatakan tersedia|$A/services/satuan_pakai.py|s@Nama belum bisa diganti agar barang tidak kehilangan satuan.\") if pakai else ada()@Nama belum bisa diganti agar barang tidak kehilangan satuan.\") if False else ada()@|$T"
 "g13 peringatan satuan terdaftar hilang|$A/services/satuan_rencana.py|s@        if ada:@        if False:@|$T"
 "g14 teks status kosong|$A/services/satuan_rencana.py|s@\"status_text\": \"Aktif\"@\"status_text\": \"\"@|$T"
 "g15 jalur ubah tanpa rencana (kolom dari badan)|$A/routers/units.py|s@kolom = \[k for k in SR.URUT if k in r\[\"nilai\"\]\]@kolom = [k for k in SR.URUT if k in body]@|$T"
 "g16 nonaktif terpakai cek pakai SEMUA bawaan|$A/services/satuan_rencana.py|s@    elif not u.get(\"is_active\", True):@    elif False:@|$T"
)
