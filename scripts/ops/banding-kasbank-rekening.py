"""Banding uji-salinan-kasbank-rekening: lama == baru untuk buat (C), ubah (U), hapus (H); SATU beda yang diputuskan (putusan A):
hapus lama 500 -> baru 400 REKENING_BERSALDO_AWAL dengan akibat DB identik. Plus cek baru (V, I)."""
import json
import sys

lama, baru = (json.load(open(f)) for f in sys.argv[1:3])
gagal, info = [], {"500->400": 0}
for bagian in ("buat", "ubah"):
    for k, v in lama[bagian].items():
        if v != baru[bagian].get(k):
            gagal.append(f"{bagian[0].upper()} {k}: lama {json.dumps(v)[:300]} != baru {json.dumps(baru[bagian].get(k))[:300]}")
for k, v in lama["hapus"].items():
    b = baru["hapus"].get(k)
    if b is None:
        gagal.append(f"H {k} tak ada di baru")
        continue
    if (v["akibat_db"], v["coa_ada"]) != (b["akibat_db"], b["coa_ada"]):
        gagal.append(f"H {k} akibat DB beda: lama {v} baru {b}")
    elif v["status"] == 500 and b["status"] == 400 and "saldo awal yang sudah dibukukan" in json.dumps(b["resp"]):
        info["500->400"] += 1
    elif (v["status"], v["resp"]) != (b["status"], b["resp"]):
        gagal.append(f"H {k} status/kalimat beda: lama {v} baru {b}")
gagal += [f"cek {c[0]} :: {c[2]}" for c in baru["cek"] if not c[1]]
print(f"buat {len(lama['buat'])} ubah {len(lama['ubah'])} hapus {len(lama['hapus'])}; hapus 500->400 (putusan A) {info['500->400']}; "
      f"cek baru {sum(1 for c in baru['cek'] if c[1])}/{len(baru['cek'])}")
for g in gagal[:40]:
    print("[GAGAL]", g)
sys.exit(1 if gagal else 0)
