"""Bukti tak-deterministik (aturan MASTER 25 Okt): LAMA vs LAMA (dua run kode SAMA). Kolom yang beda di sini -- dan HANYA di sini -- boleh
dibandingkan ada/tidaknya oleh banding. argv: lama.jsonl lama2.jsonl keluaran.json. GAGAL bila beda di luar kolom quotes cap waktu."""
import json, sys
a = [json.loads(l) for l in open(sys.argv[1])]; b = [json.loads(l) for l in open(sys.argv[2])]
assert len(a) == len(b)
kolom, lain = {}, []
for x, y in zip(a, b):
    if x["respons"] != y["respons"]: lain.append(("respons", x["q"], x["kasus"]))
    sx, sy = x["sesudah"], y["sesudah"]
    for k in set(sx) | set(sy):
        if k == "q":
            qx, qy = sx["q"] or {}, sy["q"] or {}
            for c in set(qx) | set(qy):
                if qx.get(c) != qy.get(c):
                    if (qx.get(c) is None) != (qy.get(c) is None) or not c.endswith("_at"):
                        lain.append((c, x["q"], x["kasus"]))
                    else:
                        kolom[c] = kolom.get(c, 0) + 1
        elif sx.get(k) != sy.get(k):
            lain.append((k, x["q"], x["kasus"]))
json.dump(sorted(kolom), open(sys.argv[3], "w"))
print("LAMA vs LAMA beda kolom waktu:", kolom)
print("GAGAL beda lain:", lain[:5]) if lain else print("LULUS (hanya kolom waktu)")
