"""Banding uji-salinan-void-null."""
import json
import sys

lama, baru = (json.load(open(f)) for f in sys.argv[1:3])
gagal, null = [], 0
for k, a in lama.items():
    b = baru.get(k)
    if a["null"]:
        null += 1
        pesan = b["tulis"].get("detail")
        blok = [x.get("message") for x in (b["pratinjau"] or [])]
        ok = (a["tulis"].get("status") == 500 and b["tulis"].get("status") == 400 and b["aksi"]["tersedia"] is False
              and pesan == b["aksi"]["alasan"] and blok[:1] == [pesan])
        print(f"NULL {k}: lama {a['tulis'].get('status')} -> baru {b['tulis'].get('status')} '{pesan}' | pratinjau {blok[:1]} | aksi {b['aksi']}")
        if not ok:
            gagal.append(f"NULL {k}")
    else:
        if (a["tulis"], a["pratinjau"]) != (b["tulis"], b["pratinjau"]):
            gagal.append(f"BEDA {k}: lama={a['tulis']} {a['pratinjau']} baru={b['tulis']} {b['pratinjau']}")
        if b["aksi"]["tersedia"] != (not b["pratinjau"]):
            gagal.append(f"AKSI {k}: aksi {b['aksi']} vs pratinjau {b['pratinjau']}")
n = len(lama)
print(f"tagihan {n}; status_v2 NULL {null}; lainnya identik {n - null - sum(1 for g in gagal if g.startswith('BEDA'))}/{n - null}; "
      f"aksi == pratinjau {n - null - sum(1 for g in gagal if g.startswith('AKSI'))}/{n - null}")
for g in gagal:
    print("[GAGAL]", g)
sys.exit(1 if gagal or not null else 0)
