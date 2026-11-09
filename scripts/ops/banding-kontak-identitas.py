"""Pembanding identitas_text: argv lama.json baru.json. baru == lama + identitas_text; identitas_text = bagian tak kosong dari
(kode, HP kontak_teks, kota, termin) detail itu sendiri -- dihitung ulang di sini dari medan respons, bukan dari penentu."""
import json
import sys

lama, baru = (json.load(open(f)) for f in sys.argv[1:3])
hasil, n = [], 0


def cek(nama, ok, info=""):
    hasil.append(bool(ok))
    if not ok:
        print(f"FAIL {nama} -- {str(info)[:400]}")


for tid in lama:
    for k, a in lama[tid].items():
        b = dict(baru[tid][k])
        it = b.pop("identitas_text", "HILANG")
        cek(f"{tid} {k} medan lama identik", a == b, {x for x in set(a) | set(b) if a.get(x) != b.get(x)})
        hp = (a.get("kontak_teks") or "").split(" · ")[0] if a.get("kontak_teks") and (a.get("phone") or a.get("mobile_phone")) else None
        t = a.get("termin_text")
        harap = " · ".join(x for x in [(a.get("code") or "").strip() or None, hp, (a.get("city") or "").strip() or None,
                                       t if t == "Tunai" else f"Termin {t}"] if x)
        cek(f"{tid} {k} identitas_text", it == harap, (it, harap))
        n += 1
print(f"[info] {n} kontak")
print(f"{sum(hasil)}/{len(hasil)} lulus")
sys.exit(0 if all(hasil) else 1)
