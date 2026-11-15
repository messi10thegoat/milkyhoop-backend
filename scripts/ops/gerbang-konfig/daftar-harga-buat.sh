# Konfig gerbang terfokus Daftar Harga BUAT dengan kunci idempotensi (BUG-250, 15 Nov 2026): result_id idempotency_keys (uuid) = id daftar baru, bukan teks "baru". Tanpa migrasi.
# Pemisah sabotase = "|", ekspresi sed memakai "@".
W=/root/mh-dh-fix250; CABANG=fix/daftarharga-buat-idem-250; NAMA_JENDELA=daftar-harga-buat
POLA_UNIT='harga_aksi|price_lists|idem_buat|idempotency|daftar_harga|dh_buat_idem'
HARNESS=scripts/ops/uji-salinan-daftar-harga-buat.sh
PENANDA_BERKAS=services/harga_aksi.py; PENANDA='def _uuid_atau_none('
A=backend/api_gateway/app
T=tests/unit/test_dh_buat_idem.py
SAB=(
 "f1 result_id mengirim doc_id mentah|$A/services/harga_aksi.py|s@rid = _uuid_atau_none(doc_id) or @rid = doc_id or @|$T"
 "f2 id respons tak dipakai|$A/services/harga_aksi.py|s@ or _uuid_atau_none(data.get(\"id\") if isinstance(data, dict) else None)@@|$T"
 "f3 selalu doc_id|$A/services/harga_aksi.py|s@rid = _uuid_atau_none(doc_id) or _uuid_atau_none(data.get(\"id\") if isinstance(data, dict) else None)@rid = doc_id@|$T"
 "f4 simpan menerima doc_id|$A/services/harga_aksi.py|s@, sd, prefix, resp, rid)@, sd, prefix, resp, doc_id)@|$T"
)
