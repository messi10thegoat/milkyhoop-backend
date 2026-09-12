#!/usr/bin/env python3
"""GERBANG KESEGARAN — inventaris rute vs `app.routes` yang SUNGGUHAN.

Pasangan dari `tests/unit/test_pagar_rute_izin.py`. Pembagian tugasnya:

  uji unit  -> CAKUPAN : tiap rute di inventaris punya pola, atau tercatat
  gerbang   -> KESEGARAN: inventaris masih cocok dengan aplikasi sungguhan

Tanpa gerbang ini, inventaris bisa basi DIAM-DIAM — dan itu akan mengulang
persis cacat yang membuatku menolak parser statis. Inventaris basi harus gagal
BERISIK.

Jalankan di dalam kontainer (butuh impor aplikasi):
    docker exec milkyhoop-dev-api_gateway python - < scripts/gate_izin_rute.py
"""
import re
import sys
from pathlib import Path

AKAR = Path("/app/backend/api_gateway")
INV = AKAR / "tests" / "unit" / "inventaris_rute_tulis.txt"
GD = AKAR / "tests" / "unit" / "garis_dasar_rute_tanpa_pola.txt"
MID = AKAR / "app" / "middleware" / "permission_middleware.py"

TULIS = ("POST", "PATCH", "PUT", "DELETE")
hasil = []


def baris(p):
    if not p.exists():
        return []
    return [b.strip() for b in p.read_text(encoding="utf-8").splitlines()
            if b.strip() and not b.lstrip().startswith("#")]


def hidup():
    from backend.api_gateway.app.main import app
    out = set()
    for r in app.routes:
        m = getattr(r, "methods", None)
        p = getattr(r, "path", None)
        if not m or not p:
            continue
        for x in m:
            if x in TULIS:
                out.add("%s %s" % (x, p))
    return out


def pola():
    t = MID.read_text(encoding="utf-8")
    blok = re.search(r"ROUTE_PERMISSIONS[^=]*=\s*\[(.*?)\n\]", t, re.S).group(1)
    mentah = re.findall(r'\(r"([^"]+)",\s*\[([^\]]*)\],\s*"([^"]+)",\s*"([^"]+)"\)', blok)
    return [(re.compile(p), [x.strip().strip('"').strip("'") for x in m.split(",")])
            for p, m, _, _ in mentah]


def skip():
    t = MID.read_text(encoding="utf-8")
    blok = re.search(r"SKIP_PATTERNS\s*=\s*\[(.*?)\n\]", t, re.S).group(1)
    return [re.compile(p) for p in re.findall(r"r[\"']([^\"']+)[\"']", blok)]


def catat(kode, label, ket=""):
    hasil.append(kode)
    print("  [%s] %s %s" % (kode, label, ket))


def main():
    inv = set(baris(INV))
    liv = hidup()

    # --- kontrol positif: instrumen yang menemukan nol akan hijau selamanya ---
    if len(liv) < 300 or len(inv) < 300:
        print("GERBANG TAK SAH: rute hidup=%d inventaris=%d (ambang >300)"
              % (len(liv), len(inv)))
        return 2

    # [S] KESEGARAN
    hilang = sorted(liv - inv)   # rute BARU yang belum masuk inventaris
    hantu = sorted(inv - liv)    # rute yang sudah tak ada
    catat("H" if not hilang else "X", "S1 inventaris memuat semua rute hidup",
          "" if not hilang else "%d BARU belum tercatat: %s" % (len(hilang), hilang[:5]))
    catat("H" if not hantu else "X", "S2 inventaris tak memuat rute hantu",
          "" if not hantu else "%d sudah tak ada: %s" % (len(hantu), hantu[:5]))

    # [C] CAKUPAN diukur ulang terhadap rute HIDUP, bukan inventaris
    pc, sk = pola(), skip()
    dasar = set(baris(GD))
    telanjang = []
    for b in sorted(liv):
        metode, _, jalur = b.partition(" ")
        if any(s.match(jalur) for s in sk):
            continue
        k = re.sub(r"\{[^}]+\}", "XX", jalur)
        if any(rx.match(k) and metode in mth for rx, mth in pc):
            continue
        telanjang.append(b)
    baru = sorted(set(telanjang) - dasar)
    catat("H" if not baru else "X", "C1 nol rute tulis baru tanpa pola",
          "" if not baru else "%d: %s" % (len(baru), baru[:5]))

    basi = sorted(dasar - set(telanjang))
    catat("H" if not basi else "X", "C2 garis dasar tak memuat entri basi",
          "" if not basi else "%d sudah tertutup: %s" % (len(basi), basi[:5]))

    print("\nrute tulis hidup=%d  inventaris=%d  tanpa pola=%d  garis dasar=%d"
          % (len(liv), len(inv), len(telanjang), len(dasar)))
    buruk = [h for h in hasil if h != "H"]
    print("%s: %d/%d" % ("MERAH" if buruk else "HIJAU",
                         len(hasil) - len(buruk), len(hasil)))
    return 1 if buruk else 0


if __name__ == "__main__":
    sys.exit(main())
