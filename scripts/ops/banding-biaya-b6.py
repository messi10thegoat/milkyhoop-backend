"""Banding uji-salinan-biaya-b6: BARU == LAMA kecuali anchor.vendor (== kolom DB) dan paper A4. rc 0/1."""
import copy
import json
import sys

lama, baru = (json.load(open(p)) for p in sys.argv[1:3])
gagal, vendor_isi = [], 0
if set(lama["bundel"]) != set(baru["bundel"]) or not baru["bundel"]:
    gagal.append(f"himpunan biaya beda / kosong: lama {len(lama['bundel'])} baru {len(baru['bundel'])}")
for k, b in baru["bundel"].items():
    l = lama["bundel"].get(k)
    if l is None:
        continue
    harap_v = baru["vendor_db"][k]
    if b["anchor"].get("vendor") != harap_v:
        gagal.append(f"{k} anchor.vendor {b['anchor'].get('vendor')!r} != DB {harap_v!r}")
    vendor_isi += bool(harap_v)
    docs = [x for g in b["groups"] for x in g["docs"]]
    if not docs or any(x.get("paper") != "A4" for x in docs):
        gagal.append(f"{k} paper bukan A4 di semua dok: {[x.get('paper') for x in docs]}")
    nb = copy.deepcopy(b)
    nb["anchor"].pop("vendor", None)
    for g in nb["groups"]:
        for x in g["docs"]:
            x["paper"] = None
    if nb != l:
        gagal.append(f"{k} berubah di luar vendor/paper")
print(f"biaya {len(baru['bundel'])}; vendor terisi {vendor_isi}; gagal {len(gagal)}")
for g in gagal[:30]:
    print("[GAGAL]", g)
print(f"rc={1 if gagal else 0}")
sys.exit(1 if gagal else 0)
