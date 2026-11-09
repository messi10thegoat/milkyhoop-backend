"""Banding uji-salinan-neraca-saldo: S selisih lama − baru == Σ baris salah-hitung lama PERSIS (per tenant × tanggal); cek baru."""
import json
import sys
from decimal import Decimal

lama, baru = (json.load(open(f)) for f in sys.argv[1:3])
gagal, info = [], []
for k, (ld, lk) in lama["total"].items():
    if k not in baru["total"]:
        gagal.append(f"S {k} tak ada di baru")
        continue
    bd, bk = baru["total"][k]
    (ed, ek, en), (kd, kk, kn) = lama["salah"][k]
    harap_d, harap_k = Decimal(ed) - Decimal(kd), Decimal(ek) - Decimal(kk)
    sel_d = (Decimal(str(ld)) - Decimal(str(bd))).quantize(Decimal("0.01"))
    sel_k = (Decimal(str(lk)) - Decimal(str(bk))).quantize(Decimal("0.01"))
    info.append(f"{k}: lama−baru debit {sel_d} kredit {sel_k}; lebih {en} baris (d {ed}), nonaktif {kn} baris (d {kd})")
    if (sel_d, sel_k) != (harap_d.quantize(Decimal("0.01")), harap_k.quantize(Decimal("0.01"))):
        gagal.append(f"S {k} selisih {sel_d}/{sel_k} != harap {harap_d}/{harap_k}")
gagal += [f"cek {c[0]} :: {c[2]}" for c in baru["cek"] if not c[1]]
for i in info:
    print("[S]", i)
print(f"laporan {len(lama['total'])}; S lulus {len(lama['total']) - sum(1 for g in gagal if g.startswith('S '))}/{len(lama['total'])}; "
      f"cek baru {sum(1 for c in baru['cek'] if c[1])}/{len(baru['cek'])}")
for g in gagal[:40]:
    print("[GAGAL]", g)
sys.exit(1 if gagal else 0)
