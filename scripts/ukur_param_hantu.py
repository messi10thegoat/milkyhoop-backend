#!/usr/bin/env python3
"""Ukur PARAMETER HANTU: nama kueri yang dikirim klien NYATA tapi tidak
dideklarasikan endpoint-nya — diterima, diabaikan diam-diam, dijawab 200.

Sumber kebenaran daftar parameter adalah TABEL RUTE FastAPI itu sendiri
(`app.routes` + `route.dependant.query_params`), bukan hasil membaca berkas.
Percobaan pertamaku mengurai `@router.get(...)` dan `APIRouter(prefix=...)`
dari sumber, lalu gagal memetakan 2.323 dari 2.328 permintaan (99,8%) karena
prefix sesungguhnya dipasang di main.py lewat include_router. Alat yang buta
99,8% melaporkan "nyaris tak ada masalah" — persis kesimpulan yang salah.

Lalu lintasnya dari log akses nginx, bukan permintaan karangan sendiri: kelas
cacat ini justru muncul saat yang dikirim klien berbeda dari yang kita kira.

Dijalankan DI DALAM kontainer:
    python3 ukur_param_hantu.py /log/access.log
"""
import re
import sys
from collections import Counter, defaultdict
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, "/app/backend/api_gateway")

PERM = re.compile(r'"(?:GET) (/[^" ]*)')


def tabel_rute():
    from app.main import app
    rute = []
    for r in app.routes:
        jalur = getattr(r, "path", None)
        metode = getattr(r, "methods", None) or set()
        dep = getattr(r, "dependant", None)
        if not jalur or "GET" not in metode or dep is None:
            continue
        nama = {p.alias or p.name for p in dep.query_params}
        rute.append((jalur, nama))
    return rute


def cocok(jalur, pola):
    a = [x for x in jalur.split("/") if x]
    b = [x for x in pola.split("/") if x]
    if len(a) != len(b):
        return False
    return all(pb.startswith("{") or pa == pb for pa, pb in zip(a, b))


def utama(log):
    rute = tabel_rute()
    print(f"rute GET di tabel FastAPI: {len(rute)}")
    hantu = defaultdict(Counter)
    tak_terpeta = Counter()
    total = 0
    with open(log, encoding="utf-8", errors="replace") as f:
        for baris in f:
            m = PERM.search(baris)
            if not m:
                continue
            u = urlparse(m.group(1))
            if not u.query:
                continue
            total += 1
            kandidat = [(pl, nm) for pl, nm in rute if cocok(u.path, pl)]
            if not kandidat:
                tak_terpeta[u.path] += 1
                continue
            pola, sah = min(kandidat, key=lambda k: k[0].count("{"))
            for k in parse_qs(u.query, keep_blank_values=True):
                if k not in sah:
                    hantu[pola][k] += 1

    print(f"permintaan GET berparameter di log: {total}")
    n_tp = sum(tak_terpeta.values())
    print(f"tak cocok ke rute mana pun: {len(tak_terpeta)} jalur / {n_tp} permintaan "
          f"({100*n_tp/total:.1f}%)")
    if n_tp:
        print("   contoh:", ", ".join(list(tak_terpeta)[:5]))
    print()
    if not hantu:
        print("tak ada parameter hantu.")
        return 0
    print("PARAMETER HANTU (diterima, diabaikan, dijawab 200):")
    for jalur, c in sorted(hantu.items(), key=lambda kv: -sum(kv[1].values())):
        print(f"  {jalur}")
        for k, n in c.most_common():
            print(f"      {k:22} {n:6} permintaan")
    t = sum(sum(c.values()) for c in hantu.values())
    print(f"\nTOTAL permintaan nyata dengan parameter hantu: {t}")
    print(f"Menolaknya dengan 422 hari ini = mematikan {t} permintaan.")
    return 0


if __name__ == "__main__":
    sys.exit(utama(sys.argv[1]))
