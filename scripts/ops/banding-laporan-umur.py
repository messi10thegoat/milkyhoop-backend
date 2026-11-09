"""Banding uji-salinan-laporan-umur: medan lama lama == baru PERSIS sebagai multiset; cek baru.
Urutan baris TIDAK dibandingkan: kueri umur hanya ORDER BY due_date, sehingga dokumen bertanggal jatuh tempo sama (kaos AP: 13 baris,
5 tanggal) keluar dalam urutan bebas di tiap klon -- sudah begitu sebelum L2 (tiket: tie-breaker ORDER BY)."""
import json
import sys


def kanon(v):
    """Daftar diurutkan menurut isi kanoniknya; nilai lain apa adanya."""
    if isinstance(v, dict):
        return {k: kanon(x) for k, x in v.items()}
    if isinstance(v, list):
        return sorted((kanon(x) for x in v), key=lambda x: json.dumps(x, sort_keys=True))
    return v


lama, baru = (json.load(open(f)) for f in sys.argv[1:3])
gagal = [f"BEDA medan lama {k}" for k in lama["lama"] if kanon(lama["lama"][k]) != kanon(baru["lama"].get(k))]
urut_sama = sum(1 for k in lama["lama"] if lama["lama"][k] == baru["lama"].get(k))
gagal += [f"cek {c[0]} :: {c[2]}" for c in baru["cek"] if not c[1]]
n = len(lama["lama"])
print(f"laporan {n}; medan lama sama {n - sum(1 for g in gagal if g.startswith('BEDA'))}/{n} (urutan sama {urut_sama}/{n}); "
      f"cek baru {sum(1 for c in baru['cek'] if c[1])}/{len(baru['cek'])}")
for g in gagal[:40]:
    print("[GAGAL]", g)
sys.exit(1 if gagal else 0)
