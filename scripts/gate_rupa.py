#!/usr/bin/env python3
"""Gerbang RUPA template B vs acuan pemilik: posisi, JUMLAH, dan TEBAL garis.

Kenapa ada di samping gate_geo.py: gerbang geometri lama hijau 0.3mm padahal
pemilik melihat cetakan yang belum rapi. Ia mengukur POSISI garis di zona
tetap -- bukan berapa garis yang ada, bukan setebal apa. Gerbang yang
membuktikan klaim lebih sempit daripada yang dilaporkan adalah gerbang yang
menyesatkan, dan itu kesalahanku, bukan kesalahan alat.

Semua nilai HARAP di bawah DIUKUR dari PDF acuan pada 300dpi, bukan disalin
dari deskripsi siapa pun. Dua di antaranya membantah anggapan umum:
  - garis di bawah kop acuan ada DUA (26.59 @1.20pt dan 27.31 @1.44pt),
    bukan satu;
  - tidak ada skema "bingkai luar tebal, kolom dalam tipis": garis tegak
    acuan 1.20-2.16pt dan mendatar 1.20-2.16pt -- sebaran yang sama, yakni
    satu goresan ~0.75pt yang jatuh di sub-piksel berbeda.
"""
import subprocess
import sys

import numpy as np
from PIL import Image

TOL_POSISI_MM = 2.0
TOL_TEBAL_PT = 0.6
HARAP = [  # (y_mm, tebal_pt, x0, x1) dari acuan
    (26.59, 1.20, 3.1, 206.8),
    (27.31, 1.44, 3.1, 206.8),
    (69.82, 1.92, 3.1, 206.9),
    (80.02, 2.16, 3.1, 207.0),
    (190.57, 1.92, 3.1, 207.0),
    (196.71, 1.20, 3.1, 207.0),
]
KOP_ZONA = (20.0, 30.0)
KOP_JUMLAH = 2


def muat(pdf):
    png = pdf.replace(".pdf", "_rupa.png")
    subprocess.run(["sips", "-s", "format", "png", "--resampleWidth", "2480",
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


def garis(a, ppm, panjang_min_mm=20.0, ambang=200):
    g = a < ambang
    pmin = panjang_min_mm * ppm
    baris = {}
    for y in range(g.shape[0]):
        for x0, x1 in _runs(g[y]):
            if x1 - x0 + 1 >= pmin:
                baris.setdefault(y, []).append((x0, x1))
    ys = sorted(baris)
    grup, run = [], []
    for y in ys:
        if run and y - run[-1] > 2:
            grup.append(run); run = []
        run.append(y)
    if run:
        grup.append(run)
    out = []
    for gp in grup:
        seg = [s for y in gp for s in baris[y]]
        out.append((round(float(np.mean(gp)) / ppm, 2),
                    round(len(gp) / ppm * 72 / 25.4, 2),
                    round(min(s[0] for s in seg) / ppm, 1),
                    round(max(s[1] for s in seg) / ppm, 1)))
    return out


def periksa(pdf, label, harus_hijau):
    a, ppm = muat(pdf)
    gl = garis(a, ppm)
    print(f"\n== {label} ==")
    gagal = []
    for y, t, x0, x1 in HARAP:
        dekat = min(gl, key=lambda o: abs(o[0] - y)) if gl else None
        if dekat is None:
            gagal.append(f"garis {y}mm tidak ada"); continue
        dp, dt = abs(dekat[0] - y), abs(dekat[1] - t)
        tanda = "OK " if (dp <= TOL_POSISI_MM and dt <= TOL_TEBAL_PT) else "MERAH"
        print(f"   {tanda} y={y:6.2f}->{dekat[0]:6.2f} ({dp:4.2f}mm)  tebal {t:4.2f}->{dekat[1]:4.2f}pt ({dt:4.2f})")
        if dp > TOL_POSISI_MM:
            gagal.append(f"posisi {y}mm meleset {dp:.2f}mm")
        if dt > TOL_TEBAL_PT:
            gagal.append(f"tebal di {y}mm meleset {dt:.2f}pt")
    n_kop = len([o for o in gl if KOP_ZONA[0] <= o[0] <= KOP_ZONA[1]])
    tanda = "OK " if n_kop == KOP_JUMLAH else "MERAH"
    print(f"   {tanda} jumlah garis zona kop {KOP_ZONA}: {n_kop} (harap {KOP_JUMLAH})")
    if n_kop != KOP_JUMLAH:
        gagal.append(f"garis kop {n_kop}, harap {KOP_JUMLAH}")
    print(f"   -> {'HIJAU' if not gagal else str(len(gagal)) + ' MERAH'}")
    if harus_hijau and gagal:
        return False, gagal
    if not harus_hijau and not gagal:
        return False, ["KONTROL tidak merah -- gerbang tak bisa gagal"]
    return True, gagal


if __name__ == "__main__":
    ok1, g1 = periksa(sys.argv[1], "TEMPLATE B vs acuan", True)
    ok2, _ = periksa(sys.argv[2], "KONTROL MERAH: template A (harus gagal)", False)
    print()
    if not ok1:
        print("GAGAL B:", "; ".join(g1))
    print(f"B hijau: {ok1}   kontrol merah menyala: {ok2}")
    sys.exit(0 if (ok1 and ok2) else 1)
