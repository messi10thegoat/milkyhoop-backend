"""Banding uji-salinan-rekon-lama-pagar: semua cek baru lulus (rc 0/1); hasil pohon lama dicetak sebagai bukti lubang."""
import json
import sys

lama, baru = (json.load(open(p)) for p in sys.argv[1:3])
for k, v in lama.get("lama", {}).items():
    print("LAMA", k, "status", v["hasil"]["tanpa"][0], v["hasil"]["dengan"][0], "jurnal", v["jurnal"], v["status"])
gagal = [c for c in baru["cek"] if not c[1]]
for c in gagal:
    print("GAGAL", c[0], c[2])
print(f"cek baru {len(baru['cek']) - len(gagal)}/{len(baru['cek'])}")
sys.exit(1 if gagal or not baru["cek"] else 0)
