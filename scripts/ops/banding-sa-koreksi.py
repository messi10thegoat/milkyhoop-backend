"""Banding SA-KOREKSI (mode baru). argv: baru.json [lama.json]."""
import json, sys
from decimal import Decimal as D
b = json.load(open(sys.argv[1])); g = []
def cek(c, m):
    if not c: g.append(m)
if b["jaring_sebelum"] != b["jaring_sesudah"]: g.append(f"BOCOR commit {b['jaring_sebelum']} -> {b['jaring_sesudah']}")
cek("ok" in b["pratinjau"] and b["pratinjau"]["ok"]["data"]["ok"], f"pratinjau {b['pratinjau']}")
cek(b["pratinjau_tanpa_tulis"], "pratinjau_menulis")
cek("ok" in b["balik"], f"balik {b['balik']}")
cek(b["balik_ulang"] == b["balik"], "ulang_bukan_jawaban_pertama")
cek(b["balik_kedua_tanpa_kunci"].get("galat", [0, {}])[0] == 409 and b["balik_kedua_tanpa_kunci"]["galat"][1].get("code") == "TAK_ADA_SALDO_AWAL_LAMA", f"balik_kedua {b['balik_kedua_tanpa_kunci']}")
# dua sumber: AP vendor naik sebesar -(saldo lama); GL per akun = sebelum + baris tukar jurnal lama
lama_out = D(b["ap0"]) - D(b["ap1"])
cek(D(b["ap1"]) == D(b["ap0"]) + D("1000.00"), f"AP sesudah balik {b['ap0']} -> {b['ap1']} (harap +1000)")
for kode in set(b["gl0"]) | set(b["gl1"]):
    d0, k0 = map(D, b["gl0"].get(kode, ["0", "0"])); d1, k1 = map(D, b["gl1"].get(kode, ["0", "0"]))
    harap = {"2-10100": (D(0), D(1000)), "3-50000": (D(1000), D(0))}.get(kode, (D(0), D(0)))
    cek((d1 - d0, k1 - k0) == harap, f"GL {kode} delta {(d1 - d0, k1 - k0)} harap {harap}")
cek(len(b["lama1"]) == len(b["lama0"]) == 1 and b["lama1"][0]["dibalik"] and b["lama1"][0]["status"] == "POSTED", f"jurnal lama {b['lama1']}")
cek("ok" in b["sa_baru"] and D(b["ap2"]) == D(b["ap1"]) + 5000, f"saldo awal baru {b['sa_baru']} ap {b['ap1']}->{b['ap2']}")
cek(b.get("um_buat") == "ok" and b.get("um_terap") == "ok", f"UM tak teruji {b.get('um_buat')} {b.get('um_terap')}")
vb = b.get("void_ber_um", {}).get("galat")
cek(vb and vb[0] == 400 and "uang muka" in str(vb[1]).lower(), f"void_ber_um WAJIB ditolak: {b.get('void_ber_um')}")
cek(b.get("um_lepas") == "ok" and "ok" in b.get("void_bersih", {}), f"void_bersih {b.get('void_bersih')}")
cek(D(b["ap4"]) == D(b["ap1"]), f"AP sesudah void {b['ap4']} harap {b['ap1']}")
cek("ok" in b.get("sa_baru_lagi", {}) and D(b["ap5"]) == D(b["ap1"]) + 5000, f"saldo awal baru lagi {b.get('sa_baru_lagi')}")
if len(sys.argv) > 2:
    l = json.load(open(sys.argv[2]))
    print("LAMA: sa_baru =", list(l["sa_baru"])[0], json.dumps(l["sa_baru"])[:120], "| void_ber_um =", json.dumps(l.get("void_ber_um"))[:120])
    cek(l["jaring_sebelum"] == l["jaring_sesudah"], "BOCOR commit LAMA")
for x in g: print("GAGAL", x)
print("LULUS" if not g else "GAGAL")
