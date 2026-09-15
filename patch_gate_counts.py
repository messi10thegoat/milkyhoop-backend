"""Item 4: cacah harapan gerbang diturunkan dari STRUKTUR (bukan angka ketik-tangan).
v244: rincian per-jalur -> sum. jurnal: dari daftar subjek + himpunan cabang. Nilai sama (18/13),
tak tautologis (independen len(hasil), tetap menangkap cek yang terlewat)."""
import io, sys

# v244_handler
P1 = "/root/mh-law2/scripts/gerbang_v244_handler.py"
t = io.open(P1, encoding="utf-8").read()
O1 = 'HARAP_CACAH = 18  # prasyarat 1 + j1 3 + j2 3 + j3 2 + j4 2 + j5 2 + j6 3 (handler + T3 cache + T3 outstanding) + vendor 1 + kontrol 1'
N1 = ('# cacah diturunkan dari rincian per-jalur (handler + cek T3), bukan angka ketik-tangan.\n'
      '# tiap jalur = 1 (handler berhasil) + jumlah cek fn_cek; prasyarat/vendor/kontrol = 1.\n'
      '_CEK_PER_JALUR = {"prasyarat": 1, "j1": 3, "j2": 3, "j3": 2, "j4": 2, "j5": 2, "j6": 3, "vendor": 1, "kontrol": 1}\n'
      'HARAP_CACAH = sum(_CEK_PER_JALUR.values())')
if t.count(O1) != 1:
    print("GAGAL v244 jangkar", t.count(O1)); sys.exit(1)
t = t.replace(O1, N1)
io.open(P1, "w", encoding="utf-8").write(t)

# jurnal_pelanggan_uuid
P2 = "/root/mh-law2/scripts/gerbang_jurnal_pelanggan_uuid.py"
u = io.open(P2, encoding="utf-8").read()
O2 = '''    # prasyarat 1 + ber-DP 4 + tanpa-DP 3 + ber-CN 4 + sabotase 1
    harap = 13'''
N2 = '''    # cacah diturunkan dari STRUKTUR: prasyarat 1 + per-subjek (3 dasar: 200/entries/total; +1 bila
    # subjek bercabang DP/CN) + sabotase 1. (bukan angka ketik-tangan.)
    _CABANG = {"ber-DP", "ber-NOTA-KREDIT"}
    harap = 1 + sum(3 + (1 if lbl in _CABANG else 0) for lbl, _ in subjek) + 1'''
if u.count(O2) != 1:
    print("GAGAL jurnal jangkar", u.count(O2)); sys.exit(1)
u = u.replace(O2, N2)
io.open(P2, "w", encoding="utf-8").write(u)

# verifikasi nilai sama
import ast
ns = {}
exec('_CEK_PER_JALUR = {"prasyarat": 1, "j1": 3, "j2": 3, "j3": 2, "j4": 2, "j5": 2, "j6": 3, "vendor": 1, "kontrol": 1}\nv = sum(_CEK_PER_JALUR.values())', ns)
subjek = [("ber-DP", 1), ("tanpa-DP", 1), ("ber-NOTA-KREDIT", 1)]
_CABANG = {"ber-DP", "ber-NOTA-KREDIT"}
jurnal_harap = 1 + sum(3 + (1 if lbl in _CABANG else 0) for lbl, _ in subjek) + 1
print(f"OK item4. v244 HARAP={ns['v']} (==18: {ns['v']==18}); jurnal harap={jurnal_harap} (==13: {jurnal_harap==13})")
