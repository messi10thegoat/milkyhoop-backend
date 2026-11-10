"""Banding uji-salinan-terbit-pratinjau: cek baru semua lulus + L (draf yang LOLOS di baru: journal_lines lama == baru). rc 0/1."""
import json
import sys

lama, baru = (json.load(open(p)) for p in sys.argv[1:3])
gagal = [c for c in baru["cek"] if not c[1]]
for c in gagal:
    print("GAGAL", c[0], c[2])
beda = 0
for k, v in baru["jurnal"].items():
    if v is not None and lama["jurnal"].get(k) != v:
        beda += 1
        print("GAGAL L", k, "lama", str(lama["jurnal"].get(k))[:300], "baru", str(v)[:300])
palsu_lama = sum(1 for k, v in lama["jurnal"].items() if baru["jurnal"].get(k) is None)
print(f"cek baru {len(baru['cek']) - len(gagal)}/{len(baru['cek'])} · L draf lolos lama==baru beda={beda} · "
      f"draf ditolak (lama menampilkan jurnal perkiraan, baru blok) = {palsu_lama}")
print("rc=", 1 if gagal or beda or not baru["cek"] else 0)
sys.exit(1 if gagal or beda or not baru["cek"] else 0)
