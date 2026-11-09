"""Pembanding temuan TESTER Kontak (A, C): argv = lama.json baru.json. Perubahan SAH saja:
 A daftar/detail identik; kontak_teks vendor beda HANYA untuk vendor ber-HP tanpa telepon (baru memuat HP)
 R riwayat: jumlah sama; kejadian beda HANYA baris bentuk lama -> baru tanpa " -> " / "Merged customer" / UUID
 C PATCH: lama satu baris "Pelanggan diperbarui"+details == baru baris per medan (kalimat, lama, baru); baris lain identik
   gabung: lama "Merged customer X (uuid)…" -> baru target "Pelanggan X digabung ke pelanggan ini" + sumber "Digabung ke pelanggan Y\""""
import json
import re
import sys

lama, baru = (json.load(open(f)) for f in sys.argv[1:3])
UUID_RE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
hasil, n = [], {"A": 0, "R": 0, "R_beda": 0, "C": 0}


def cek(nama, ok, info=""):
    hasil.append(bool(ok))
    if not ok:
        print(f"FAIL {nama} -- {str(info)[:500]}")


def teks(label, baru_):
    if label == "Status aktif":
        return "Diaktifkan" if str(baru_).strip().lower() in ("true", "1") else "Dinonaktifkan"
    return f"{label} diubah"


def hp_ok(a, b, hp):
    beda = {k for k in set(a) | set(b) if a.get(k) != b.get(k)}
    return not beda or (hp and beda == {"kontak_teks"} and b["kontak_teks"])


for tid in lama:
    L, B = lama[tid], baru[tid]
    hp = set(B["A_vhp"])
    for vid in L["A_vl"]:
        cek(f"{tid} A_vl {vid}", hp_ok(L["A_vl"][vid], B["A_vl"][vid], vid in hp), (L["A_vl"][vid].get("kontak_teks"), B["A_vl"][vid].get("kontak_teks")))
        n["A"] += 1
    for k in sorted(L):
        if k.startswith("A_vd:"):
            cek(f"{tid} {k}", hp_ok(L[k], B[k], k[5:] in hp), (L[k].get("kontak_teks"), B[k].get("kontak_teks")))
            n["A"] += 1
        elif k.startswith("A_cd:"):
            cek(f"{tid} {k} identik", L[k] == B[k])
            n["A"] += 1
        elif k.startswith("R_"):
            cek(f"{tid} {k} jumlah sama", len(L[k]) == len(B[k]), (len(L[k]), len(B[k])))
            for x, y in zip(L[k], B[k]):
                if x != y:
                    n["R_beda"] += 1
                    mentah = " -> " in x or "Merged customer" in x or UUID_RE.search(x) or x.startswith("Status aktif")
                    cek(f"{tid} {k} beda hanya baris lama", mentah and " -> " not in y and "Merged customer" not in y and not UUID_RE.search(y),
                        (x, y))
            n["R"] += 1
        elif k.startswith("C_ca:"):
            lp = [r for r in L[k] if r["description"] == "Pelanggan diperbarui"]
            bp = [r for r in B[k] if r["type"] == "updated" and r["field_name"]]
            cek(f"{tid} {k} baris lain identik", [r for r in L[k] if r not in lp] == [r for r in B[k] if r not in bp])
            harap = sorted((teks(*(lambda lab, nil: (lab, nil.rsplit(' -> ', 1)[1]))(*b.split(": ", 1))), b.split(": ", 1)[1].rsplit(" -> ", 1)[0],
                            b.split(": ", 1)[1].rsplit(" -> ", 1)[1]) for r in lp for b in r["details"].split("\n"))
            nyata = sorted((r["description"], r["old_value"], r["new_value"]) for r in bp)
            cek(f"{tid} {k} per medan == details lama", len(lp) == 1 and harap == nyata, (harap, nyata))
            n["C"] += 1
    if "C_g" in L:
        g, h = L["C_g"], B["C_g"]
        cek(f"{tid} C_g hasil sama", g["ok"] == h["ok"], (g, h))
        if g["ok"]:
            src, tgt = h["nama"][1], h["nama"][0]
            lm = [r for r in g["target"] if r["type"] == "merge"]
            cek(f"{tid} C_g lama Inggris+UUID", len(lm) == 1 and lm[0]["description"].startswith(f"Merged customer {src} ("))
            cek(f"{tid} C_g baru target", [r["description"] for r in h["target"] if r["type"] == "merge"] == [f"Pelanggan {src} digabung ke pelanggan ini"])
            cek(f"{tid} C_g baru sumber", [r["description"] for r in h["source"] if r["type"] == "merge"] == [f"Digabung ke pelanggan {tgt}"])
            cek(f"{tid} C_g baris lain identik", [r for r in g["target"] if r["type"] != "merge"] == [r for r in h["target"] if r["type"] != "merge"])
            n["C"] += 1
print(f"[info] {n}")
print(f"{sum(hasil)}/{len(hasil)} lulus")
sys.exit(0 if all(hasil) else 1)
