"""Pemilih rute BERBASIS TABEL untuk sapuan L1 V247 (bukan berbasis modul).

BATAS BAWAH, disebut terang: grep teks nama tabel (credit_notes / customer_deposits) di SELURUH routers/ + services/.
Buta terhadap nama tabel yang dirakit saat jalan dan pemanggilan lewat lebih dari satu lompatan helper yg tak
menyebut nama tabel. Karena itu setiap rute terpilih DIKONFIRMASI lewat eksekusi dgn pencatat SQL (gerbang_v247_l1).

Langkah:
 1. semua kemunculan nama tabel di routers/ + services/ -> fungsi pembungkus (def terdekat di atas)
 2. fungsi yang BUKAN handler rute -> cari handler rute yang memanggilnya (satu & dua lompatan, lintas berkas)
 3. petakan ke tabel rute hidup (app.routes): endpoint __module__ + __name__
Kontrol kelengkapan WAJIB: sales_invoices.get_applicable_deposits & credit_notes.list_credit_notes terpilih.
Keluaran: /tmp/rute_terpilih.json
"""
import json
import os
import re
import sys

sys.path.insert(0, "/app/backend/api_gateway")
ROOT = "/app/backend/api_gateway/app"
POLA = re.compile(r"\b(credit_notes|customer_deposits)\b")

fungsi_menyentuh = set()   # (modul, nama)
isi_berkas = {}
for sub in ("routers", "services"):
    for d, _, fs in os.walk(os.path.join(ROOT, sub)):
        if "__pycache__" in d:
            continue
        for f in fs:
            if not f.endswith(".py"):
                continue
            p = os.path.join(d, f)
            mod = "app." + p[len(ROOT) + 1:-3].replace("/", ".")
            b = open(p, encoding="utf-8", errors="ignore").read().split("\n")
            isi_berkas[mod] = b
            for i, ln in enumerate(b):
                if POLA.search(ln) and not ln.strip().startswith("#"):
                    j = i
                    while j >= 0 and not re.match(r"\s*(async\s+)?def\s+(\w+)", b[j]):
                        j -= 1
                    if j >= 0:
                        fungsi_menyentuh.add((mod, re.match(r"\s*(async\s+)?def\s+(\w+)", b[j]).group(2)))

nama_menyentuh = {n for _, n in fungsi_menyentuh}


def pemanggil(nama_set):
    """handler/fungsi yang badannya memanggil salah satu nama."""
    hasil = set()
    for mod, b in isi_berkas.items():
        cur = None
        for ln in b:
            m = re.match(r"\s*(async\s+)?def\s+(\w+)", ln)
            if m:
                cur = m.group(2)
                continue
            if cur and any(re.search(rf"\b{re.escape(n)}\s*\(", ln) for n in nama_set):
                hasil.add((mod, cur))
    return hasil


lompat1 = pemanggil(nama_menyentuh)
lompat2 = pemanggil({n for _, n in lompat1} - nama_menyentuh)
kandidat = fungsi_menyentuh | lompat1 | lompat2

from app.main import app  # noqa: E402
terpilih = []
for r in app.routes:
    ep = getattr(r, "endpoint", None)
    if not ep:
        continue
    key = (ep.__module__, ep.__name__)
    if key in kandidat:
        terpilih.append({"modul": ep.__module__, "nama": ep.__name__, "path": r.path, "metode": sorted(getattr(r, "methods", []) or [])})

print(f"fungsi menyentuh (teks): {len(fungsi_menyentuh)} · +pemanggil 1 lompat: {len(lompat1)} · +2 lompat: {len(lompat2)}")
print(f"rute hidup terpilih: {len(terpilih)} dari {len(app.routes)}")
per_mod = {}
for t in terpilih:
    per_mod.setdefault(t["modul"].split(".")[-1], []).append(f'{"/".join(t["metode"])} {t["nama"]}')
for m, v in sorted(per_mod.items()):
    print(f"  {m}: {len(v)}")
    for x in v:
        print(f"     {x}")
k1 = any(t["nama"] == "get_applicable_deposits" for t in terpilih)
k2 = any(t["nama"] == "list_credit_notes" for t in terpilih)
print(f"KONTROL K1 get_applicable_deposits={k1} K2 list_credit_notes={k2} -> {'SAH' if k1 and k2 else 'PEMILIH BUTA'}")
json.dump(terpilih, open("/tmp/rute_terpilih.json", "w"), indent=1)
