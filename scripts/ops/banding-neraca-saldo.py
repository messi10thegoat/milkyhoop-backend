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
# Z: /full sel per sel -- yang berubah HANYA pecahan sen yang dulu dipotong int()
berubah = 0
for k, v in lama.get("full", {}).items():
    nb = baru.get("full", {}).get(k)
    if nb is None:
        gagal.append(f"Z {k} tak ada di baru")
        continue
    lv, bv = Decimal(v), Decimal(nb)
    if lv != bv:
        berubah += 1
        print(f"[Z] {k}: lama {lv} -> baru {bv}")
        tid_pid, akun, kol = k.rsplit("|", 2)
        if akun == "RINGKASAN":
            # total = Σ baris: lama menjumlah baris yang SUDAH dipotong int(), jadi selisih total == Σ selisih sel akun kolom itu
            # (bukan < 1). Kolom total_opening_debit <-> opening_debit, dst.
            kolom = kol[len("total_"):]
            harap = sum((Decimal(baru["full"][x]) - Decimal(lama["full"][x]) for x in lama["full"]
                         if x.startswith(tid_pid + "|") and x.endswith("|" + kolom) and "|RINGKASAN|" not in x
                         and x in baru["full"]), Decimal("0"))
            if bv - lv != harap:
                gagal.append(f"Z {k} selisih total {bv - lv} != Σ selisih baris {harap}")
        elif not (abs(bv - lv) < 1 and lv == int(bv)):
            gagal.append(f"Z {k} berubah lebih dari pecahan sen: lama {lv} baru {bv}")
if set(baru.get("full", {})) - set(lama.get("full", {})):
    gagal.append(f"Z sel baru tanpa pasangan lama: {sorted(set(baru['full']) - set(lama['full']))[:5]}")
print(f"[Z] sel /full {len(lama.get('full', {}))}; berubah {berubah} (semua wajib pecahan sen)")
gagal += [f"cek {c[0]} :: {c[2]}" for c in baru["cek"] if not c[1]]
for i in info:
    print("[S]", i)
print(f"laporan {len(lama['total'])}; S lulus {len(lama['total']) - sum(1 for g in gagal if g.startswith('S '))}/{len(lama['total'])}; "
      f"cek baru {sum(1 for c in baru['cek'] if c[1])}/{len(baru['cek'])}")
for g in gagal[:40]:
    print("[GAGAL]", g)
sys.exit(1 if gagal else 0)
