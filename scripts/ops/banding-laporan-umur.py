"""Banding uji-salinan-laporan-umur: medan lama lama == baru PERSIS; cek baru."""
import json
import sys

lama, baru = (json.load(open(f)) for f in sys.argv[1:3])
gagal = [f"BEDA medan lama {k}" for k in lama["lama"] if lama["lama"][k] != baru["lama"].get(k)]
gagal += [f"cek {c[0]} :: {c[2]}" for c in baru["cek"] if not c[1]]
print(f"laporan {len(lama['lama'])}; medan lama sama {len(lama['lama']) - sum(1 for g in gagal if g.startswith('BEDA'))}/{len(lama['lama'])}; "
      f"cek baru {sum(1 for c in baru['cek'] if c[1])}/{len(baru['cek'])}")
for g in gagal[:40]:
    print("[GAGAL]", g)
sys.exit(1 if gagal else 0)
