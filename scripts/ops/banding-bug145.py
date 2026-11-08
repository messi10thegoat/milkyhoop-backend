"""Pembanding BUG-145: argv = lama.json baru.json. SO belum selesai: SEMUA medan identik. SO selesai: 6 titik = max(invoiced − tertutup, 0)
(satu angka), posisi daftar == posisi dokumen; nyawit SO-2611-0019: 288.000 "Sebagian ditagih" -> 0 "Lunas"."""
import json
import sys
from decimal import Decimal as D

lama, baru = (json.load(open(f)) for f in sys.argv[1:3])
hasil = []


def cek(nama, ok, info=""):
    hasil.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + nama + (f" -- {info}" if info else ""))


for tid, b in baru.items():
    l = lama[tid]
    cek(f"{tid} SO sama", set(l) == set(b), f"{len(b)} SO")
    n_sel = n_ubah = 0
    for no, x in sorted(b.items()):
        y = l[no]
        if x["status"] != "completed":
            if x != y:
                cek(f"{tid} {no} ({x['status']}) TIDAK berubah", False, str({k: (y[k], x[k]) for k in x if x[k] != y[k]}))
            continue
        n_sel += 1
        harap = f"{max(D(x['invoiced']) - D(x['tertutup']), D(0)):.2f}"
        angka = (x["sisa_daftar"], x["sisa_per_so"], x["remaining"], x["rekap_sisa"], f"{D(x['dok_balance'] or 0):.2f}")
        cek(f"{tid} {no} selesai: 5 angka == max(difaktur − tertutup, 0) = {harap}", all(a == harap for a in angka),
            f"{angka}; lama {y['sisa_daftar']} {y['posisi']!r} -> {x['posisi']!r}")
        cek(f"{tid} {no} posisi daftar == dokumen; rekap lunas <=> sisa 0", x["posisi"] == x["dok_posisi"]
            and (x["rekap_status"] == "lunas") == (D(harap) == 0))
        n_ubah += x != y
    print(f"[info] {tid}: SO selesai {n_sel}, berubah {n_ubah}; SO lain identik")
cek("SO-2611-0019 nyawit 288.000 Sebagian ditagih -> 0 Lunas",
    (lama["pt-kita-nyawit-bersama"]["SO-2611-0019"]["sisa_daftar"], lama["pt-kita-nyawit-bersama"]["SO-2611-0019"]["posisi"],
     baru["pt-kita-nyawit-bersama"]["SO-2611-0019"]["sisa_daftar"], baru["pt-kita-nyawit-bersama"]["SO-2611-0019"]["posisi"])
    == ("288000.00", "Sebagian ditagih", "0.00", "Lunas"))
print(f"HASIL {sum(hasil)}/{len(hasil)} PASS")
sys.exit(0 if all(hasil) else 1)
