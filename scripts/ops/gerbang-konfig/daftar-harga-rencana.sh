# Konfig gerbang terfokus BUG-275 (Tinjau tambah/ubah SATU baris harga memberi `rencana` dari/ke/selisih_harga dari penentu server). Cabang puncak rantai Daftar Harga.
# Hanya memuat SAB (dibaca konfig paket daftar-harga-rantai.sh); tanpa migrasi. Pemisah sabotase = "|", sed memakai "@".
W=/root/mh-dh-riwayat-252; CABANG=feat/daftarharga-riwayat-252; NAMA_JENDELA=daftar-harga-rencana
A=backend/api_gateway/app
T=tests/unit/test_dh_rencana_275.py
SAB=(
 "p1 dari tak diisi|$A/services/harga_riwayat.py|s@dari = {k: lama\[k\] for k in bentuk_ke} if lama else None@dari = None@|$T"
 "p2 selisih salah arah|$A/services/harga_riwayat.py|s@round(bentuk_ke\[\"harga\"\] - dari\[\"harga\"\], 2)@round(dari[\"harga\"] - bentuk_ke[\"harga\"], 2)@|$T"
 "p3 rencana tak bersyarat|$A/routers/price_lists.py|s@\*\*({\"rencana\": rencana} if rencana is not None else {})@**({\"rencana\": rencana})@|$T"
 "p4 kode badan tak menang|$A/services/harga_riwayat.py|s@(item.item_code or (p\[\"kode\"\] if p else None))@(p[\"kode\"] if p else None)@|$T"
 "p5 unit_text mentah|$A/services/harga_riwayat.py|s@HD.unit_text(item.unit, p\[\"dasar\"\] if p else None)@item.unit@|$T"
 "p6 min_price tak dibawa|$A/services/harga_riwayat.py|s@\"min_price\": float(item.min_price) if item.min_price is not None else None,@\"min_price\": None,@|$T"
 "p7 mulai tak dibawa|$A/services/harga_riwayat.py|s@\"mulai\": item.start_date.isoformat() if item.start_date else None,@\"mulai\": None,@|$T"
)
