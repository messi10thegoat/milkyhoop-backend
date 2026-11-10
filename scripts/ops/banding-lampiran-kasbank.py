"""Banding uji-salinan-lampiran-kasbank: jenis lama LAMA == BARU (keluaran pembaca identik), semua cek baru (D, X, P) lulus; urutan dicatat."""
import json
import sys

lama, baru = (json.load(open(f)) for f in sys.argv[1:3])
gagal = [f"L {k}: lama {json.dumps(v)[:300]} != baru {json.dumps(baru['lama_jenis'].get(k))[:300]}"
         for k, v in lama["lama_jenis"].items() if v != baru["lama_jenis"].get(k)]
if set(lama["lama_jenis"]) != set(baru["lama_jenis"]):
    gagal.append("L himpunan dokumen sampel beda")
gagal += [f"cek {c[0]} :: {c[2]}" for c in baru["cek"] if not c[1]]
u = baru["urutan"]
print(f"jenis lama {len(lama['lama_jenis'])} (berlampiran {sum(1 for v in lama['lama_jenis'].values() if v[0] == 200 and v[1].get('jumlah'))}); "
      f"cek baru {sum(1 for c in baru['cek'] if c[1])}/{len(baru['cek'])}; urutan sama dengan pabrik {sum(u.values())}/{len(u)} (info)")
for g in gagal[:40]:
    print("[GAGAL]", g)
sys.exit(1 if gagal or not baru["cek"] else 0)
