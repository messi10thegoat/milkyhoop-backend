"""Banding uji-salinan-laporan-psak: medan LAMA lama == baru (dibulatkan sen; ekor float persis dihitung), cek BARU, waktu p50."""
import json
import sys


def ratakan(x, jalur=""):
    if isinstance(x, dict):
        for k, v in x.items():
            yield from ratakan(v, f"{jalur}.{k}")
    elif isinstance(x, list):
        for i, v in enumerate(x):
            yield from ratakan(v, f"{jalur}[{i}]")
    else:
        yield jalur, x


lama, baru = (json.load(open(f)) for f in sys.argv[1:3])
gagal, ekor, n = [], 0, 0
for k, v in lama["lama"].items():
    a, b = dict(ratakan(v)), dict(ratakan(baru["lama"].get(k, {})))
    if set(a) != set(b):
        gagal.append(f"KUNCI beda {k}: {sorted(set(a) ^ set(b))[:6]}")
        continue
    for j, x in a.items():
        n += 1
        y = b[j]
        if isinstance(x, float) or isinstance(y, float):
            if round(float(x), 2) != round(float(y), 2):
                gagal.append(f"NILAI {k}{j}: lama {x} baru {y}")
            elif x != y:
                ekor += 1
        elif x != y:
            gagal.append(f"BEDA {k}{j}: lama {x!r} baru {y!r}")
gagal += [f"cek {c[0]} :: {c[2]}" for c in baru["cek"] if not c[1]]
p50 = lambda xs: sorted(xs)[len(xs) // 2] if xs else 0  # noqa: E731
print(f"laporan {len(lama['lama'])}; medan lama {n}; sama (sen) {n - sum(1 for g in gagal if g.startswith(('NILAI', 'BEDA')))}/{n}; "
      f"ekor float berbeda (sen sama) {ekor}; cek baru {sum(1 for c in baru['cek'] if c[1])}/{len(baru['cek'])}")
print(f"GET p50 ms: lama {p50(lama['waktu']):.1f} | baru {p50(baru['waktu']):.1f}")
for g in gagal[:60]:
    print("[GAGAL]", g)
sys.exit(1 if gagal else 0)
