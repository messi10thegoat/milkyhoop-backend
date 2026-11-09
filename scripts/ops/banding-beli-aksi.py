"""Banding uji-salinan-beli-aksi: detail lama == baru (tanpa aksi_tersedia), hasil penulis/pratinjau lama == baru, cek BARU, waktu p50/p95
per modul (ambang putusan MASTER §5.2: p95 naik >80 ms ATAU >30% -> opt-in ?aksi_tersedia=1)."""
import json
import sys


def p(xs, q):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(round(q * (len(xs) - 1))))] if xs else 0.0


lama, baru = (json.load(open(f)) for f in sys.argv[1:3])
gagal, kalimat_rp = [], []
for k, v in lama["detail"].items():
    if baru["detail"].get(k) != v:
        gagal.append(f"detail beda {k}")
for k, v in lama["tulis"].items():
    for a, h in v.items():
        b = baru["tulis"].get(k, {}).get(a)
        if b != h and k.startswith("penerimaan:") and a == "rpposting" and not k.endswith(":draft") and not h["ok"] and b and \
                not b["ok"] and h["status"] == b["status"] and "tidak bisa diposting" in str(b["detail"]):
            kalimat_rp.append(f"{k}: {h['detail']} -> {b['detail']}")  # PUTUSAN 1 MASTER 10 Nov: tolak sama, kalimat status
            continue
        if b != h:
            gagal.append(f"tulis beda {k} {a}: lama={json.dumps(h)[:300]} baru={json.dumps(baru['tulis'].get(k, {}).get(a))[:300]}")
gagal += [f"cek {c[0]} :: {c[2]}" for c in baru["cek"] if not c[1]]
n = len(lama["detail"])
print(f"dokumen {n}; detail sama {n - sum(1 for g in gagal if g.startswith('detail'))}/{n}; "
      f"hasil penulis/pratinjau {sum(len(v) for v in lama['tulis'].values())}; cek baru {sum(1 for c in baru['cek'] if c[1])}/{len(baru['cek'])}")
for m in lama["waktu"]:
    wl, wb = lama["waktu"][m], baru["waktu"].get(m, [])
    p50l, p95l, p50b, p95b = p(wl, .5), p(wl, .95), p(wb, .5), p(wb, .95)
    lewat = p95b - p95l > 80 or (p95l and p95b / p95l - 1 > 0.30)
    print(f"GET detail {m:12s} ms: lama p50 {p50l:6.1f} p95 {p95l:6.1f} | baru p50 {p50b:6.1f} p95 {p95b:6.1f} | p95 {p95b - p95l:+6.1f} ms "
          f"({(p95b / p95l - 1) * 100 if p95l else 0:+.0f}%){'  << AMBANG TERLAMPAUI' if lewat else ''}")
print("biaya aksi_tersedia MURNI (pohon baru, klon sama, terselang off/on, 5 putaran):")
for m, ww in baru.get("waktu_onoff", {}).items():
    p95o, p95n, p50o, p50n = p(ww["off"], .95), p(ww["on"], .95), p(ww["off"], .5), p(ww["on"], .5)
    lewat = p95n - p95o > 80 or (p95o and p95n / p95o - 1 > 0.30)
    print(f"  {m:12s} off p50 {p50o:6.1f} p95 {p95o:6.1f} | on p50 {p50n:6.1f} p95 {p95n:6.1f} | p95 {p95n - p95o:+6.1f} ms "
          f"({(p95n / p95o - 1) * 100 if p95o else 0:+.0f}%){'  << AMBANG TERLAMPAUI' if lewat else ''}")
bt = baru.get("belum_terukur", [])
print(f"BELUM TERUKUR (izin PolicyEngine tak ada di kontainer uji; tak diakali): {len(bt)} -- kunci {sorted({x[2] for x in bt})}")
print(f"mode kunci transaksi READ ONLY: {baru.get('mode_kunci')}")
ul = baru.get("ulang_idempoten", [])
print(f"ULANG IDEMPOTEN (penentu pratinjau memblok 'sudah dibatalkan'; penulis 200 tanpa ubah): {len(ul)} -- {sorted({x[2] for x in ul})}")
print(f"PUTUSAN 1 (RP /post status sebelum sisa): penerimaan non-draf tolak lama==baru, kalimat saja berubah: {len(kalimat_rp)}")
for x in kalimat_rp[:20]:
    print("  ", x)
print("waktu per kunci (pohon baru, dalam transaksi RO) p50/p95/maks ms:")
for k, xs in sorted(baru.get("waktu_kunci", {}).items()):
    print(f"  {k:26s} n={len(xs):3d} p50 {p(xs, .5):6.1f} p95 {p(xs, .95):6.1f} maks {max(xs):6.1f}")
print(f"tersedia tapi penulis/pratinjau menolak (isian/data/lingkungan, ditinjau): {len(baru['isian'])}")
for x in baru["isian"][:80]:
    print("  ", x)
for g in gagal[:100]:
    print("[GAGAL]", g)
sys.exit(1 if gagal else 0)
