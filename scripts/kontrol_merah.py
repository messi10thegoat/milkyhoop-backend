"""KONTROL MERAH untuk ukur_tonjolan.py.

Gerbang yang tak pernah bisa merah tidak menjaga apa pun. Di sini tonjolan
0,2mm DIGAMBAR ke dalam raster yang sudah terbukti bersih; kalau detektor
tetap melaporkan bersih, detektornya yang rusak, bukan cetakannya.
"""
import sys

import numpy as np
from PIL import Image

from geo import muat
from ukur_tonjolan import AMBANG, dasar_tabel, tonjolan

SUMBER = "n1-1.png"
PALSU = "kontrol-tonjolan.png"
KOLOM_MM = (14.14, 36.4, 57.9)   # kolom dalam yang HARUS berhenti di dasar
TINGGI_MM = 0.2                  # persis besaran yang dikeluhkan pemilik

a, ppm = muat(SUMBER)
g = a < AMBANG
grp = dasar_tabel(g, ppm)
assert grp is not None, "kontrol tak bisa dibuat: dasar tabel tak ketemu"
bawah = grp[-1]

b = a.copy()
n_px = max(1, int(round(TINGGI_MM * ppm)))
for xmm in KOLOM_MM:
    x = int(round(xmm * ppm))
    b[bawah + 1:bawah + 1 + n_px, x:x + 3] = 0
Image.fromarray(b).save(PALSU)
print(f"kontrol: {len(KOLOM_MM)} tonjolan {TINGGI_MM}mm ({n_px}px) di bawah y={bawah/ppm:.2f}mm")

y, t = tonjolan(PALSU)
print(f"detektor melihat: {len(t)} tonjolan")
for o in t:
    print(f"    x={o['x']:7.2f} menonjol {o['tinggi_mm']:.3f}mm")
if len(t) != len(KOLOM_MM):
    print(f"GAGAL: kontrol merah tidak terdeteksi ({len(t)} != {len(KOLOM_MM)})")
    sys.exit(1)
print("OK: detektor BISA merah pada 0,2mm.")
