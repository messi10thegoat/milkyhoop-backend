"""Uji SENTUHAN di raster: adakah tinta teks menempel garis?

Kenapa di raster dan bukan di pohon tata letak: kotak baris teks memuat spasi
antarbaris (line-height 2.1 pada 8pt = 5.9mm untuk huruf 2.8mm), jadi ia
melaporkan sentuhan yang tak pernah tercetak; sedangkan taksiran "tinggi
huruf = font_size" membuatnya BUTA terhadap sentuhan yang sungguh tercetak.
Aku sudah keliru ke dua arah itu. Halaman cetak adalah wasitnya.

Cara: untuk tiap garis mendatar, periksa pita 0.1-0.5mm di atas dan di
bawahnya. Tinta di situ yang membentuk ruas PENDEK (< 15mm) adalah teks yang
menempel; ruas panjang adalah garis lain.
"""
import sys

import numpy as np

sys.path.insert(0, ".")
from geo import muat, _runs  # noqa: E402
from tebal import analisis  # noqa: E402


def sentuhan(nama, x_maks_mm=206.0):
    a, ppm = muat(nama)
    g = a < 200
    hasil = []
    for o in analisis(nama)[0]:
        y0 = int((o["y"] - o["tebal_pt"] * 25.4 / 72 / 2) * ppm) - 1
        y1 = int((o["y"] + o["tebal_pt"] * 25.4 / 72 / 2) * ppm) + 1
        for pita, nama_pita in (((y0 - 5, y0 - 1), "atas"), ((y1 + 1, y1 + 5), "bawah")):
            band = g[max(0, pita[0]):pita[1], :int(x_maks_mm * ppm)]
            if band.size == 0:
                continue
            baris = band.any(axis=0)
            for r0, r1 in _runs(baris, celah=3):
                lebar = (r1 - r0) / ppm
                if 0.6 < lebar < 15.0:
                    hasil.append((o["y"], nama_pita, round(r0 / ppm, 1), round(lebar, 1)))
    return hasil


if __name__ == "__main__":
    for n in sys.argv[1:]:
        h = sentuhan(n)
        print(f"\n{n}: {len(h)} sentuhan")
        for x in h[:8]:
            print(f"   garis y={x[0]:7.2f} pita {x[1]:5} mulai x={x[2]:6.1f} lebar {x[3]}mm")
