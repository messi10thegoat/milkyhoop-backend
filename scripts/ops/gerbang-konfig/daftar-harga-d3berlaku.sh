# Konfig gerbang terfokus BUG-278 (D3 Ubah harga massal: SATU baris sumber per kunci = baris yang BERLAKU pada tanggal berlaku baru; deterministik; tak ada tumbukan kunci).
# services/harga_massal.py (baris_efektif + rencana_barang + ukur_rencana). TANPA migrasi. Cabang turunan puncak rantai (547e5649a). Pemisah sabotase = "|", sed memakai "@".
W=/root/mh-dh-d3berlaku-278; CABANG=feat/daftarharga-d3-berlaku-278; NAMA_JENDELA=daftar-harga-d3berlaku
POLA_UNIT='harga_massal|dh_d3_berlaku|daftar_harga_d3|dh_berjangka|bulk_price|bulk_f1|dh_harga_minimum|dh_riwayat|daftar_harga'
HARNESS=scripts/ops/uji-salinan-daftar-harga-d3berlaku.sh
PENANDA_BERKAS=services/harga_massal.py; PENANDA='def baris_efektif('
A=backend/api_gateway/app
M=$A/services/harga_massal.py
T=tests/unit/test_dh_d3_berlaku_278.py
SAB=(
 "t1 semua baris jadi sumber|$M|s@^    baris = baris_efektif(baris, tanggal_sumber)@    baris = baris@|$T"
 "t2 tanggal sumber selalu hari ini|$M|s@tanggal_sumber = mulai if (mulai and mulai > hari_ini) else hari_ini@tanggal_sumber = hari_ini@|$T"
 "t3 seri memilih mulai terlama|$M|s@str(b\[\"id\"\])) > ((cur.get(\"start_date\") or date.min)@str(b[\"id\"])) < ((cur.get(\"start_date\") or date.min)@|$T"
 "t4 end_date diabaikan|$M|s@ or (akhir_b is not None and akhir_b < tanggal)@@|$T"
 "t5 tak berlaku bukan blok bernama|$M|s@\"DH_TAK_BERLAKU_PADA_TANGGAL\"@\"DH_X\"@|$T"
 "t6 kunci satuan sensitif huruf|$M|s@\" \".join(str(b.get(\"unit\") or \"\").split()).lower()@str(b.get(\"unit\") or \"\")@|$T"
 "t7 urutan baca tanpa start_date|$M|s@pli.min_quantity, pli.start_date NULLS FIRST, pli.id@pli.min_quantity@|$T"
 "t8 end_date tak dibaca|$M|s@pli.start_date, pli.end_date, COALESCE@pli.start_date, COALESCE@|$T"
 "t9 urutan hasil tak berkunci|$M|s@key=lambda b: (_kunci_baris(b)\[0\], _kunci_baris(b)\[1\], str(b\[\"id\"\]))@key=lambda b: str(b[\"id\"])@|$T"
)
