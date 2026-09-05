"""Deteksi teks yang MENEMBUS garis, di raster.

Riwayat kenapa begini: dua cara berbasis pohon tata letak gagal ke dua arah.
Kotak baris teks (line-height) melaporkan tabrakan yang tak tercetak;
taksiran tinggi huruf membuat gerbang BUTA pada tabrakan yang tercetak. Dan
pada border-collapse, WeasyPrint mengatribusikan garis gabungan ke sel yang
JAUH lebih lebar daripada goresan yang benar-benar digambar (terukur: border
dilaporkan x 3.3..176.1 padahal raster hanya 145.0..206.8).

Uji yang MEMBEDAKAN: untuk tiap goresan mendatar yang benar-benar tergambar,
periksa kolom x di dalam rentangnya; kalau ada tinta yang MENERUS dari
0.5mm di atas goresan sampai 0.5mm di bawahnya, berarti ada yang menembus
garis itu. Garis tegak tabel juga menembus -- itu wajar, jadi kolom yang
berimpit dengan garis tegak dikecualikan.
"""
import sys

import numpy as np

sys.path.insert(0, ".")
from geo import muat  # noqa: E402
from tebal import analisis, tegak  # noqa: E402


def tembusan(nama, toleransi_mm=0.35):
    a, ppm = muat(nama)
    g = a < 200
    kolom_tegak = [x for x, _ in tegak(nama, 82, 215, panjang_min_mm=25.0)]
    hasil = []
    for o in analisis(nama)[0]:
        # Ruas pendek bukan garis tabel melainkan TEKS yang kebetulan rapat
        # (di acuan, judul "DESCRIPTIONS OF GOODS" terbaca sebagai ruas 26mm).
        if o["x1"] - o["x0"] < 30.0:
            continue
        t_mm = o["tebal_pt"] * 25.4 / 72
        y_at = int((o["y"] - t_mm / 2 - 0.5) * ppm)
        y_bw = int((o["y"] + t_mm / 2 + 0.5) * ppm)
        if y_at < 0 or y_bw >= g.shape[0]:
            continue
        band = g[y_at:y_bw, int(o["x0"] * ppm):int(o["x1"] * ppm)]
        menerus = band.all(axis=0)
        xs = np.where(menerus)[0]
        buruk = []
        for x in xs:
            xm = (x + o["x0"] * ppm) / ppm
            if any(abs(xm - k) <= toleransi_mm for k in kolom_tegak):
                continue
            buruk.append(round(xm, 1))
        # >= 3 kolom: satu-dua kolom adalah garis tegak yang meleset sedikit
        # dari toleransi, bukan huruf.
        if len(buruk) >= 3:
            hasil.append((o["y"], len(buruk), buruk[:4]))
    return hasil


if __name__ == "__main__":
    kode = 0
    for n in sys.argv[1:]:
        h = tembusan(n)
        print(f"\n{n}: {len(h)} garis ditembus teks")
        for y, jml, contoh in h:
            print(f"   garis y={y:7.2f} ditembus di {jml} kolom, mis. x={contoh}")
        if h:
            kode = 1
    sys.exit(kode)
