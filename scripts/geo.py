"""Geometri halaman faktur: cari RUAS garis, bukan rentang ujung-ke-ujung.

Dua cara sebelumnya sama-sama menipuku:
  1) ujung-kiri..ujung-kanan  -> baris TEKS di dalam tabel terbaca "selebar
     tabel", karena garis tegak kolom gelap di setiap y.
  2) syarat kesinambungan pada rentang itu -> garis kotak yang KEBETULAN
     sebaris dengan teks blok bank ditolak, padahal garisnya ada.
Yang benar: cari deretan piksel gelap BERSAMBUNG (ruas) dan laporkan yang
panjangnya >= panjang_min. Teks tak pernah membentuk ruas sepanjang itu.
"""
import numpy as np
from PIL import Image


def muat(p, lebar_mm=210.0):
    im = Image.open(p)
    if im.mode in ("RGBA", "LA", "P"):
        im = im.convert("RGBA")
        im = Image.alpha_composite(Image.new("RGBA", im.size, (255,) * 4), im)
    a = np.asarray(im.convert("L"))
    return a, a.shape[1] / lebar_mm


def _runs(mask, celah=6):
    """celah=6px (~0.5mm) DISENGAJA.

    Dengan border-collapse: separate tiap sel menggambar bordernya sendiri,
    dan di sambungan antar sel tersisa celah 1-3 piksel. Dengan celah=2 garis
    yang MATA LIHAT sebagai satu garis lurus terpecah jadi potongan pendek
    dan dilaporkan HILANG -- alat ukur yang menuduh cetakan yang benar.
    """
    idx = np.where(mask)[0]
    out, run = [], []
    for i in idx:
        if run and i - run[-1] > celah:
            out.append((run[0], run[-1])); run = []
        run.append(i)
    if run:
        out.append((run[0], run[-1]))
    return out


def garis(a, ppm, arah="h", panjang_min_mm=40.0, tebal_maks_mm=1.2, ambang=200):
    # ambang 200, bukan 140: garis hairline yang jatuh di antara dua piksel
    # terender abu-abu ~90-190. Ambang ketat menjawab "seberapa PEKAT", bukan
    # "di mana" -- dan untuk soal posisi ia melaporkan garis yang jelas ada
    # sebagai HILANG. Kepekatan diperiksa terpisah.
    g = a < ambang
    if arah == "v":
        g = g.T
    pmin = panjang_min_mm * ppm
    kasar = []
    for y in range(g.shape[0]):
        for x0, x1 in _runs(g[y]):
            if x1 - x0 + 1 >= pmin:
                kasar.append((y, x0, x1))
    # gabung baris-baris berdekatan yang ruasnya bertumpang tindih
    hasil = []
    for y, x0, x1 in kasar:
        for h in hasil:
            if y - h[-1] <= max(2, tebal_maks_mm * ppm) and not (x1 < h[1] - 5 or x0 > h[2] + 5):
                h[0].append(y); h[1] = min(h[1], x0); h[2] = max(h[2], x1); h[-1] = y
                break
        else:
            hasil.append([[y], x0, x1, y])
    return [(round(float(np.mean(h[0])) / ppm, 1), round(h[1] / ppm, 1), round(h[2] / ppm, 1)) for h in hasil]


if __name__ == "__main__":
    import sys
    for p in sys.argv[1:]:
        a, ppm = muat(p)
        print(f"\n=== {p} ({ppm:.2f}px/mm) ===")
        print("MENDATAR (y, x0, x1):")
        for t in garis(a, ppm, "h"):
            print(f"   y={t[0]:6}  x {t[1]:6}..{t[2]:6}  panjang {round(t[2]-t[1],1)}")
        print("TEGAK (x, y0, y1):")
        for t in garis(a, ppm, "v", panjang_min_mm=30.0):
            print(f"   x={t[0]:6}  y {t[1]:6}..{t[2]:6}  panjang {round(t[2]-t[1],1)}")
