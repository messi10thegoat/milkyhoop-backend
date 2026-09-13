"""INVENTARIS IZIN (baca-saja): tabel rute HIDUP (introspeksi app.routes) × ROUTE_PERMISSIONS/SKIP_PATTERNS middleware.
Keluaran:
  A rute tanpa pola (lolos middleware tanpa cek) — dipisah tulis/baca, dan apakah ada penjaga LAIN (dependency / di handler)
  B pola tanpa rute (pola mati / ejaan salah)
  C method tak tercakup (path cocok pola untuk method lain, method ini tak ada)
  D rute di SKIP_PATTERNS
Kontrol alat: K1 rute yang PASTI terpetakan (/api/sales-invoices GET) harus terbaca terpetakan; K2 pola sengaja karangan harus
terbaca 'tanpa rute'. Penjaga lain dideteksi dari nama dependency (rekursif) dan sumber handler (penanda), dilabeli HEURISTIK.
"""
import inspect
import json
import re
import sys
from collections import Counter, defaultdict

sys.path.insert(0, "/app/backend/api_gateway")
from fastapi.routing import APIRoute  # noqa: E402

from app.main import app  # noqa: E402
from app.middleware import permission_middleware as pm  # noqa: E402

TULIS = {"POST", "PUT", "PATCH", "DELETE"}
PENANDA = re.compile(r"policy_engine|\.can\(|require_permission|check_permission|assert_required_roles|require_role|require_owner|"
                     r"require_active_membership|has_permission|permission_required|RequirePermission|verify_owner|is_owner")

pola = [(re.compile(p), m, mod, act, p) for p, m, mod, act in pm.ROUTE_PERMISSIONS]
lewati = [re.compile(p) for p in pm.SKIP_PATTERNS]


def contoh(path):
    return re.sub(r"\{[^}]+\}", "00000000-0000-4000-8000-000000000001", path)


def dep_nama(dep, acc):
    for d in dep.dependencies:
        if d.call is not None:
            acc.add(getattr(d.call, "__name__", type(d.call).__name__))
        dep_nama(d, acc)
    return acc


rute = []
for r in app.routes:
    if not isinstance(r, APIRoute):
        continue
    for meth in sorted(r.methods - {"HEAD", "OPTIONS"}):
        rute.append((r, meth))

hasil_A, hasil_D, cocok_pola = [], [], Counter()
for r, meth in rute:
    p = contoh(r.path)
    if any(x.match(p) for x in lewati):
        hasil_D.append((meth, r.path))
        continue
    kena = [pp for (c, ms, mod, act, pp) in pola if meth in ms and c.match(p)]
    if kena:
        cocok_pola[kena[0]] += 1   # middleware memakai kecocokan PERTAMA
        continue
    deps = dep_nama(r.dependant, set())
    try:
        src = inspect.getsource(r.endpoint)
    except (OSError, TypeError):
        src = ""
    dep_jaga = sorted(d for d in deps if PENANDA.search(d))
    src_jaga = sorted(set(PENANDA.findall(src)))
    lain_method = sorted({act_m for (c, ms, mod, act, pp) in pola if c.match(p) for act_m in ms} - {meth})
    hasil_A.append({"method": meth, "path": r.path, "module": r.endpoint.__module__.replace("app.routers.", ""),
                    "tulis": meth in TULIS, "dep_jaga": dep_jaga, "src_jaga": src_jaga, "pola_method_lain": lain_method})

semua_contoh = [(meth, contoh(r.path)) for r, meth in rute]
hasil_B = [pp + " " + ",".join(ms) for (c, ms, mod, act, pp) in pola
           if not any(meth in ms and c.match(p) for meth, p in semua_contoh)]
hasil_C = [a for a in hasil_A if a["pola_method_lain"]]

# kontrol alat
k1 = not any(a["path"] == "/api/sales-invoices" and a["method"] == "GET" for a in hasil_A)
k2_c = re.compile(r"^/api/pola-karangan-gerbang$")
k2 = not any(k2_c.match(p) for _, p in semua_contoh)

tulis_tanpa = [a for a in hasil_A if a["tulis"]]
tulis_tanpa_jaga = [a for a in tulis_tanpa if not a["dep_jaga"] and not a["src_jaga"]]
print(json.dumps({
    "kontrol": {"K1_sales_invoices_GET_terpetakan": k1, "K2_pola_karangan_tanpa_rute": k2},
    "cacah": {"rute_method_total": len(rute), "SKIP": len(hasil_D), "terpetakan": sum(cocok_pola.values()),
              "A_tanpa_pola": len(hasil_A), "A_tulis": len(tulis_tanpa), "A_tulis_tanpa_penjaga_lain_heuristik": len(tulis_tanpa_jaga),
              "A_baca": len(hasil_A) - len(tulis_tanpa), "B_pola_tanpa_rute": len(hasil_B), "C_method_tak_tercakup": len(hasil_C),
              "pola_total": len(pola), "modul_router_tanpa_pola_satu_pun": None},
}, indent=1))
per_modul = defaultdict(lambda: [0, 0, 0])
for a in hasil_A:
    per_modul[a["module"]][0 if a["tulis"] else 1] += 1
    if a["tulis"] and not a["dep_jaga"] and not a["src_jaga"]:
        per_modul[a["module"]][2] += 1
modul_terpetakan = set()
for r, meth in rute:
    p = contoh(r.path)
    if any(c.match(p) and meth in ms for (c, ms, *_ ) in pola):
        modul_terpetakan.add(r.endpoint.__module__.replace("app.routers.", ""))
print("MODUL (tulis_tanpa_pola, baca_tanpa_pola, tulis_tanpa_penjaga_lain) — modul sama sekali tanpa pola ditandai *")
for mod, (w, rd, wn) in sorted(per_modul.items(), key=lambda x: -x[1][2]):
    print(f"  {'*' if mod not in modul_terpetakan else ' '} {mod:40} {w:4} {rd:4} {wn:4}")
print("B POLA TANPA RUTE:")
for b in hasil_B:
    print("  ", b)
print("C METHOD TAK TERCAKUP (contoh 25):")
for c in hasil_C[:25]:
    print("  ", c["method"], c["path"], "pola hanya untuk", c["pola_method_lain"])
print("D SKIP:", Counter(p.split('/')[2] if p.count('/') > 1 else p for _, p in hasil_D))
print("CONTOH TULIS TANPA POLA & TANPA PENJAGA LAIN (40):")
for a in tulis_tanpa_jaga[:40]:
    print("  ", a["method"], a["path"])
json.dump({"A": hasil_A, "B": hasil_B, "D": hasil_D}, open("/tmp/inventaris_izin.json", "w"), indent=1)
