# Konfig gerbang terfokus label baris perubahan pratinjau (18 Nov 2026): LR.baris {field,label,before,after,before_text,after_text} di Satuan/Gudang/Kategori. ADITIF, tanpa migrasi.
# Pemisah sabotase = "|", sed memakai "@".
W=/root/mh-label; CABANG=feat/label-pratinjau; NAMA_JENDELA=label-pratinjau
POLA_UNIT='label_rencana|satuan_g3|gudang_g3|k2_kategori|k3_kategori|k3b|k4_kategori|bug282|akun_sah|izin_rute'
HARNESS=scripts/ops/uji-salinan-akun-sah.sh
PENANDA_BERKAS=services/label_rencana.py; PENANDA='def baris('
A=backend/api_gateway/app
T=tests/unit/test_label_rencana.py
TS=tests/unit/test_satuan_g3.py
TG=tests/unit/test_gudang_g3.py
SAB=(
 "l1 satuan ubah tanpa teks label|$A/services/satuan_rencana.py|s@LR.baris(k, LABEL\[k\], u\[k\], nilai\[k\])@{\"field\": k, \"label\": LABEL[k], \"before\": u[k], \"after\": nilai[k]}@|$TS"
 "l2 teks kosong bukan strip|$A/services/label_rencana.py|s@^KOSONG = .*@KOSONG = \"\"@|$T"
 "l3 gudang negara tanpa nama|$A/services/gudang_rencana.py|s@LR.teks(GD.negara_text(sebelum))@LR.teks(sebelum)@|$TG"
 "l4 kategori status teks terbalik|$A/services/kategori_aksi.py|s@\"Nonaktif\" if ke_aktif else \"Aktif\", \"Aktif\" if ke_aktif else \"Nonaktif\"@\"Aktif\" if ke_aktif else \"Nonaktif\", \"Nonaktif\" if ke_aktif else \"Aktif\"@|$T"
 "l5 kategori akun teks tujuan rusak|$A/services/kategori_akun.py|s@sekarang\[j\]\[\"text\"\] if sekarang\[j\] else None, f\"{a@sekarang[j][\"text\"] if sekarang[j] else None, f\"x{a@|$T"
 "l6 kategori gabung label salah|$A/services/kategori_aksi.py|s@LR.baris(\"gabung\", \"Gabung\", sumber@LR.baris(\"gabung\", \"Pindah\", sumber@|$T"
)
