"""Pembanding AS-G2e: keluaran harness pohon LAMA vs BARU (argv: lama.jsonl baru.jsonl). Syarat MASTER: respons + keadaan DB IDENTIK per (SO x
kasus); riwayat SALES_ORDER_UPDATED <= 1; pratinjau draf (BARU) tanpa tulis, after/lines/blocks == hasil PATCH. Keluar 1 bila ada beda."""
import json
import sys

ANGKA = ("subtotal", "tax_amount", "total_amount", "discount_amount", "shipping_amount", "dp_percent", "dp_amount")
lama = {(h["so"], h["kasus"]): h for h in map(json.loads, open(sys.argv[1]))}
baru = {(h["so"], h["kasus"]): h for h in map(json.loads, open(sys.argv[2]))}
gagal = []
if set(lama) != set(baru) or not lama:
    gagal.append(f"himpunan kasus beda/kosong: lama {len(lama)} baru {len(baru)}")
f = lambda v: None if v is None else round(float(v), 2)  # noqa: E731
for k in sorted(set(lama) & set(baru)):
    a, b = lama[k], baru[k]
    for kunci in ("respons", "sesudah", "riwayat_baru"):
        if a[kunci] != b[kunci]:
            gagal.append(f"{k} {kunci} LAMA != BARU")
    if b["riwayat_baru"] > 1:
        gagal.append(f"{k} riwayat bertambah {b['riwayat_baru']} (>1)")
    pv = b.get("pratinjau") or {}
    if not b.get("pratinjau_tanpa_tulis"):
        gagal.append(f"{k} pratinjau MENULIS")
    if "galat" in pv:
        gagal.append(f"{k} pratinjau draf galat {pv['galat']}")
        continue
    ok_patch = "galat" not in b["respons"]
    if ok_patch:
        if pv.get("blocks"):
            gagal.append(f"{k} pratinjau blok {pv['blocks']} tapi PATCH sukses")
        so = b["sesudah"]["so"]
        beda = {x: (pv["after"].get(x), so.get(x)) for x in ANGKA if f(pv["after"].get(x)) != f(so.get(x))}
        if beda:
            gagal.append(f"{k} pratinjau.after != PATCH {beda}")
        lt_pv = sorted(f(x.get("line_total")) for x in pv.get("lines", []))
        lt_db = sorted(f(x.get("line_total")) for x in b["sesudah"]["items"])
        if lt_pv != lt_db:
            gagal.append(f"{k} pratinjau.lines != baris PATCH {lt_pv} vs {lt_db}")
    else:
        pesan = b["respons"]["galat"][1]
        pesan = pesan.get("message") if isinstance(pesan, dict) else pesan
        if not pv.get("blocks") or pv["blocks"][0]["message"] != pesan:
            gagal.append(f"{k} PATCH galat '{pesan}' tapi pratinjau blok {pv.get('blocks')}")
n_galat = sum("galat" in h["respons"] for h in baru.values())
print(f"kasus {len(baru)} (PATCH galat {n_galat}); beda {len(gagal)}")
for g in gagal[:25]:
    print("FAIL", g)
print("SELESAI_BANDING", "LULUS" if not gagal else "GAGAL")
sys.exit(1 if gagal else 0)
