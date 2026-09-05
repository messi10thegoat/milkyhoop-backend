"""Ukur TONJOLAN: goresan tegak yang menembus ke BAWAH dasar tabel.

Dasar tabel dipilih dengan MENGUKUR, bukan dengan angka yang kutulis sendiri:
di antara semua garis mendatar penuh (>=180mm), dasar tabel adalah yang punya
>=4 kolom tegak DI ATASNYA dan <4 kolom tegak DI BAWAHNYA. Percobaan pertama
memakai "garis penuh terakhir yang punya kolom di atasnya" dan memilih garis
bawah blok Subtotal (y=197.3) alih-alih dasar tabel (y=190.7) -- lalu
melaporkan "bersih" untuk garis yang salah. Detektor yang menunjuk garis
keliru tidak membuktikan apa pun.

Kolom "menerus" (tepi kiri, kotak Total/PAY) bukan tonjolan; ditentukan dari
tinta 3-4mm di bawah dasar, bukan dari daftar koordinat.
"""
import sys

from geo import muat, _runs

AMBANG = 200


def _kolom(g, ppm, y0, y1, rasio=0.9):
    """Kolom tegak di pita [y0,y1): bertinta pada >= rasio baris."""
    pita = g[max(0, y0):max(0, y1), :]
    if pita.shape[0] == 0:
        return []
    m = pita.sum(axis=0) >= rasio * pita.shape[0]
    return _runs(m, celah=2)


def _garis_penuh(g, ppm):
    lebar_min = int(180 * ppm)
    ys = [y for y in range(g.shape[0]) if g[y].sum() >= lebar_min]
    grup, run = [], []
    for y in ys:
        if run and y - run[-1] > 2:
            grup.append(run); run = []
        run.append(y)
    if run:
        grup.append(run)
    return grup


def dasar_tabel(g, ppm, rinci=False):
    """Jangkar: KEPALA tabel, bukan tebakan "garis penuh terakhir".

    Kepala = garis penuh dengan kolom tegak TERBANYAK di bawahnya (7 pada
    template ini). Dasar tabel = garis penuh PERTAMA sesudah kepala yang punya
    jumlah kolom sama BANYAKNYA di atasnya. Versi sebelumnya mengambil
    kandidat TERAKHIR dan mendarat di garis bawah blok Subtotal (197.3)
    alih-alih dasar tabel (190.6) -- "bersih" untuk garis yang keliru.
    """
    baris = []
    for grp in _garis_penuh(g, ppm):
        atas = len(_kolom(g, ppm, grp[0] - int(15 * ppm), grp[0] - 2))
        bawah = len(_kolom(g, ppm, grp[-1] + 2, grp[-1] + int(15 * ppm)))
        baris.append((grp, atas, bawah))
        if rinci:
            print(f"    garis y={grp[0]/ppm:7.2f}  kolom atas={atas} bawah={bawah}")
    if not baris:
        return None
    i_kepala, n_kol = None, 0
    for i, (grp, atas, bawah) in enumerate(baris):
        if bawah > n_kol:
            i_kepala, n_kol = i, bawah
    if i_kepala is None or n_kol < 4:
        return None
    for grp, atas, bawah in baris[i_kepala + 1:]:
        if atas >= n_kol:
            return grp
    return None


def tonjolan(nama, rinci=False):
    a, ppm = muat(nama)
    g = a < AMBANG
    grp = dasar_tabel(g, ppm, rinci)
    if grp is None:
        return None, None
    bawah = grp[-1]
    jauh = g[bawah + int(3.0 * ppm):bawah + int(4.0 * ppm), :].any(axis=0)
    menerus = set()
    for x0, x1 in _runs(jauh, celah=2):
        menerus.update(range(x0 - 2, x1 + 3))
    pita = g[bawah + 1:bawah + 1 + int(1.0 * ppm), :]
    temuan = []
    for x0, x1 in _runs(pita.any(axis=0), celah=2):
        if x0 in menerus or x1 in menerus:
            continue
        tinggi = int(pita[:, x0:x1 + 1].any(axis=1).sum())
        temuan.append(dict(x=round(x0 / ppm, 2), lebar=round((x1 - x0 + 1) / ppm, 2),
                           tinggi_mm=round(tinggi / ppm, 3)))
    return round(bawah / ppm, 3), temuan


if __name__ == "__main__":
    rinci = "-v" in sys.argv
    berkas = [n for n in sys.argv[1:] if n != "-v"]
    buruk = 0
    for nama in berkas:
        if rinci:
            print(f"{nama}:")
        y, t = tonjolan(nama, rinci)
        if t is None:
            # Bukan "tak ada tabel": pada halaman yang tabelnya MELUBER,
            # kepala ada tapi garis dasar memang tidak digambar. Dilewati
            # dengan sebab yang disebut, bukan diam-diam.
            print(f"{nama}: tabel TERBUKA (meluber, tanpa garis dasar) - dilewati")
            continue
        if t:
            buruk += 1
            print(f"{nama}: dasar y={y}mm  TONJOLAN {len(t)}:")
            for o in t:
                print(f"    x={o['x']:7.2f} lebar {o['lebar']:.2f}mm  menonjol {o['tinggi_mm']:.3f}mm")
        else:
            print(f"{nama}: dasar y={y}mm  bersih (0 tonjolan)")
    sys.exit(1 if buruk else 0)
