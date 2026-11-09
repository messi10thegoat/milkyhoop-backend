"""Banding uji-salinan-so-aksi: detail lama == baru (tanpa aksi_tersedia), hasil penulis lama == baru, cek BARU, waktu p50/p95."""
import json
import sys


def p(xs, q):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(round(q * (len(xs) - 1))))] if xs else 0.0


lama, baru = (json.load(open(f)) for f in sys.argv[1:3])
gagal = []
for k, v in lama["detail"].items():
    if baru["detail"].get(k) != v:
        gagal.append(f"detail beda {k}")
for k, v in lama["tulis"].items():
    for a, h in v.items():
        if baru["tulis"].get(k, {}).get(a) != h:
            gagal.append(f"tulis beda {k} {a}: lama={json.dumps(h)[:300]} baru={json.dumps(baru['tulis'].get(k, {}).get(a))[:300]}")
gagal += [f"cek {c[0]} :: {c[2]}" for c in baru["cek"] if not c[1]]
n = len(lama["detail"])
print(f"SO {n}; detail sama {n - sum(1 for g in gagal if g.startswith('detail'))}/{n}; "
      f"penulis {sum(len(v) for v in lama['tulis'].values())} hasil; cek baru {sum(1 for c in baru['cek'] if c[1])}/{len(baru['cek'])}")
wl, wb = lama["waktu"], baru["waktu"]
p50l, p95l, p50b, p95b = p(wl, .5), p(wl, .95), p(wb, .5), p(wb, .95)
print(f"GET detail SO ms: lama p50 {p50l:.1f} p95 {p95l:.1f} | baru p50 {p50b:.1f} p95 {p95b:.1f} | naik p95 {p95b - p95l:+.1f} ms "
      f"({(p95b / p95l - 1) * 100 if p95l else 0:+.0f}%)")
if p95b - p95l > 80 or (p95l and p95b / p95l - 1 > 0.30):
    print("AMBANG KINERJA TERLAMPAUI (>80 ms atau >30%): aksi_tersedia -> opt-in ?aksi_tersedia=1 (putusan MASTER §5.2)")
print(f"mode kunci transaksi READ ONLY: {baru.get('mode_kunci')}")
print(f"tersedia tapi penulis menolak (isian/lingkungan, ditinjau): {len(baru['isian'])}")
for x in baru["isian"][:60]:
    print("  ", x)
for g in gagal[:80]:
    print("[GAGAL]", g)
sys.exit(1 if gagal else 0)
