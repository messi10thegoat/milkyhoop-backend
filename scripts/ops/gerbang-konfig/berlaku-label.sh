# Konfig gerbang berlaku-label (rencana.berlaku{} berlabel; turunan label-pratinjau) (18 Nov 2026): LR.baris {field,label,before,after,before_text,after_text} di Satuan/Gudang/Kategori. ADITIF, tanpa migrasi.
# Pemisah sabotase = "|", sed memakai "@".
W=/root/mh-label2; CABANG=feat/berlaku-label; NAMA_JENDELA=berlaku-label
POLA_UNIT='berlaku_label|bawaan_berlaku|form_barang|label_rencana|satuan_g3|gudang_g3|k2_kategori|k3_kategori|k3b|k4_kategori|bug282|akun_sah|izin_rute'
HARNESS=scripts/ops/uji-salinan-label-pratinjau.sh
PENANDA_BERKAS=services/bawaan_berlaku.py; PENANDA='LABEL_BUTIR ='
A=backend/api_gateway/app
T=tests/unit/test_berlaku_label.py
SAB=(
 "v1 butir terisi tanpa label|$A/services/bawaan_berlaku.py|s@r\[\"label\"\] = LABEL_BUTIR\[b\]@r[\"label\"] = \"\"@|$T"
 "v2 peta label semua butir kosong|$A/services/bawaan_berlaku.py|s@hasil\[\"label\"\] = dict(LABEL_BUTIR)@hasil[\"label\"] = {}@|$T"
 "v3 label HPP huruf kecil|$A/services/label_rencana.py|s@\"hpp\": \"Akun HPP\"@\"hpp\": \"Akun hpp\"@|$T"
 "v4 label pajak beli salah|$A/services/label_rencana.py|s@\"beli\": \"Pajak pembelian\"@\"beli\": \"Pajak beli\"@|$T"
 "v5 kategori tak memakai label satu sumber|$A/services/kategori_akun.py|s@LR.baris(f\"akun_{j}\", LR.LABEL_AKUN\[j\], sekarang\[j\]\[\"text\"\] if@LR.baris(f\"akun_{j}\", \"Akun\", sekarang[j][\"text\"] if@|$T"
)
