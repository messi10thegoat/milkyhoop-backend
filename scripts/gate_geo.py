#!/usr/bin/env python3
"""Gerbang GEOMETRI template B terhadap PDF acuan pemilik.

Yang dituntut BUKAN piksel identik (isinya memang beda faktur), melainkan
POSISI garis dan blok yang sama dalam toleransi. Dijalankan di Mac karena
rasterisasi (sips) dan PIL/numpy ada di sini, bukan di image gateway.

KONTROL MERAH: template A dibandingkan ke acuan yang sama harus MELESET jauh.
Tanpa kontrol itu, gerbang yang selalu hijau tak membuktikan apa pun.
"""
import subprocess
import sys

import numpy as np
from PIL import Image

TOLERANSI_MM = 2.0
ACUAN_GARIS = [27.0, 69.7, 79.9, 190.7, 196.7]  # yang ada di KEDUA faktur
ACUAN_KOLOM = [3.3, 14.3, 36.5, 58.0, 145.2, 176.2, 206.6]


def raster(pdf, png, lebar=2480):
    subprocess.run(["sips", "-s", "format", "png", "--resampleWidth", str(lebar),
                    "--out", png, pdf], capture_output=True, check=True)
    im = Image.open(png)
    if im.mode in ("RGBA", "LA", "P"):
        im = im.convert("RGBA")
        im = Image.alpha_composite(Image.new("RGBA", im.size, (255,) * 4), im)
    a = np.asarray(im.convert("L"))
    return a, a.shape[1] / 210.0


def _runs(mask, celah=6):
    idx = np.where(mask)[0]
    out, run = [], []
    for i in idx:
        if run and i - run[-1] > celah:
            out.append((run[0], run[-1])); run = []
        run.append(i)
    if run:
        out.append((run[0], run[-1]))
    return out


def garis(a, ppm, arah="h", panjang_min_mm=40.0, ambang=200):
    g = a < ambang
    if arah == "v":
        g = g.T
    pmin = panjang_min_mm * ppm
    kasar = [(y, x0, x1) for y in range(g.shape[0])
             for x0, x1 in _runs(g[y]) if x1 - x0 + 1 >= pmin]
    hasil = []
    for y, x0, x1 in kasar:
        for h in hasil:
            if y - h[3] <= 4 and not (x1 < h[1] - 5 or x0 > h[2] + 5):
                h[0].append(y); h[1] = min(h[1], x0); h[2] = max(h[2], x1); h[3] = y
                break
        else:
            hasil.append([[y], x0, x1, y])
    return [round(float(np.mean(h[0])) / ppm, 1) for h in hasil]


def banding(pdf, label):
    a, ppm = raster(pdf, pdf.replace(".pdf", "_gate.png"))
    gm = garis(a, ppm, "h")
    gv = garis(a, ppm, "v", panjang_min_mm=30.0)
    print(f"\n{label}")
    d_maks = 0.0
    for harap in ACUAN_GARIS:
        dekat = min(gm, key=lambda y: abs(y - harap)) if gm else None
        d = abs(dekat - harap) if dekat is not None else 999
        d_maks = max(d_maks, d)
        print(f"   garis y={harap:6.1f}  ketemu {dekat if dekat is not None else '-':>6}  selisih {d:5.1f}mm")
    for harap in ACUAN_KOLOM:
        dekat = min(gv, key=lambda x: abs(x - harap)) if gv else None
        d = abs(dekat - harap) if dekat is not None else 999
        d_maks = max(d_maks, d)
        print(f"   kolom x={harap:6.1f}  ketemu {dekat if dekat is not None else '-':>6}  selisih {d:5.1f}mm")
    print(f"   ==> selisih TERBESAR {d_maks:.1f}mm")
    return d_maks


if __name__ == "__main__":
    b, a_ = sys.argv[1], sys.argv[2]
    d_b = banding(b, "TEMPLATE B vs acuan")
    d_a = banding(a_, "KONTROL MERAH: template A vs acuan (harus MELESET)")
    print()
    ok = d_b <= TOLERANSI_MM
    kontrol = d_a > TOLERANSI_MM * 2
    print(f"B dalam toleransi {TOLERANSI_MM}mm: {ok} (selisih {d_b:.1f}mm)")
    print(f"KONTROL A meleset jauh: {kontrol} (selisih {d_a:.1f}mm)")
    sys.exit(0 if (ok and kontrol) else 1)
