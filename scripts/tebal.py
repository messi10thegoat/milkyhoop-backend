"""Ukur garis mendatar LENGKAP: posisi, ujung, dan TEBAL (px -> pt)."""
import numpy as np, sys
sys.path.insert(0, ".")
from geo import muat, _runs

def analisis(nama, ambang=200, panjang_min_mm=20.0):
    a, ppm = muat(nama)
    g = a < ambang
    pmin = panjang_min_mm * ppm
    baris = {}
    for y in range(g.shape[0]):
        for x0, x1 in _runs(g[y]):
            if x1 - x0 + 1 >= pmin:
                baris.setdefault(y, []).append((x0, x1))
    # kelompokkan baris berurutan jadi satu garis
    ys = sorted(baris)
    garis, run = [], []
    for y in ys:
        if run and y - run[-1] > 2:
            garis.append(run); run = []
        run.append(y)
    if run: garis.append(run)
    out = []
    for grp in garis:
        seg = [s for y in grp for s in baris[y]]
        x0 = min(s[0] for s in seg); x1 = max(s[1] for s in seg)
        tebal_px = len(grp)
        out.append(dict(y=round(float(np.mean(grp))/ppm, 2),
                        x0=round(x0/ppm, 1), x1=round(x1/ppm, 1),
                        tebal_mm=round(tebal_px/ppm, 3),
                        tebal_pt=round(tebal_px/ppm*72/25.4, 2)))
    return out, ppm

for nama in sys.argv[1:]:
    out, ppm = analisis(nama)
    print(f"\n=== {nama} ({ppm:.2f}px/mm) ===")
    for o in out:
        print(f"  y={o['y']:7.2f}  x {o['x0']:6.1f}..{o['x1']:6.1f}  panjang {round(o['x1']-o['x0'],1):6}  tebal {o['tebal_pt']:5.2f}pt")

def tegak(nama, y0, y1, ambang=200, panjang_min_mm=30.0):
    a, ppm = muat(nama)
    g = (a < ambang)[int(y0*ppm):int(y1*ppm)].T
    pmin = panjang_min_mm * ppm
    kol = {}
    for x in range(g.shape[0]):
        for a0, a1 in _runs(g[x]):
            if a1 - a0 + 1 >= pmin:
                kol.setdefault(x, []).append((a0, a1))
    xs = sorted(kol); grup, run = [], []
    for x in xs:
        if run and x - run[-1] > 2:
            grup.append(run); run = []
        run.append(x)
    if run: grup.append(run)
    return [(round(float(np.mean(gp))/ppm, 1), round(len(gp)/ppm*72/25.4, 2)) for gp in grup]
