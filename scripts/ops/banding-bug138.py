"""Pembanding BUG-138: argv = lama.json baru.json."""
import json
import sys

lama, baru = (json.load(open(f)) for f in sys.argv[1:3])
hasil = []


def cek(nama, ok, info=""):
    hasil.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + nama + (f" -- {info}" if info else ""))


for tid, b in baru.items():
    l = lama[tid]
    cek(f"{tid} kunci pratinjau sama", set(l["pratinjau"]) == set(b["pratinjau"]), f"{len(b['pratinjau'])} pratinjau")
    n_baris = 0
    for k, pb in b["pratinjau"].items():
        pl = l["pratinjau"][k]
        sb = pb.get("stock") or []
        sl = pl.get("stock") or []
        tanpa_unit = [{x: y for x, y in r.items() if x != "unit"} for r in sb]
        cek(f"{tid} {k} nilai lain TIDAK berubah (lama == baru tanpa unit; can_proceed sama)",
            sl == tanpa_unit and pl.get("can_proceed") == pb.get("can_proceed") and pl.get("galat") == pb.get("galat"),
            f"{len(sb)} baris stok")
        cek(f"{tid} {k} unit lama absen", all("unit" not in r for r in sl))
        salah = [(r["product"], r.get("unit"), b["dasar"].get(r["product_id"])) for r in sb if r.get("unit") != b["dasar"].get(r["product_id"])]
        cek(f"{tid} {k} unit == satuan dasar barang", not salah, str(salah[:3]) + " " + str([(r["product"], r["quantity"], r.get("unit")) for r in sb][:3]))
        n_baris += len(sb)
    print(f"[info] {tid}: baris stok berunit = {n_baris}")
print(f"HASIL {sum(hasil)}/{len(hasil)} PASS")
sys.exit(0 if all(hasil) and sum(len((v.get('stock') or [])) for t in baru.values() for v in t['pratinjau'].values()) > 0 else 1)
