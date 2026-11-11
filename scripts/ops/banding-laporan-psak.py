"""Banding uji-salinan-laporan-psak: medan LAMA lama == baru (dibulatkan sen; ekor float persis dihitung), cek BARU, waktu p50."""
import json
import sys


def ratakan(x, jalur=""):
    if isinstance(x, dict):
        for k, v in x.items():
            yield from ratakan(v, f"{jalur}.{k}")
    elif isinstance(x, list):
        for i, v in enumerate(x):
            yield from ratakan(v, f"{jalur}[{i}]")
    else:
        yield jalur, x


lama, baru = (json.load(open(f)) for f in sys.argv[1:3])
gagal, ekor, n, daftar_ekor, diputuskan = [], 0, 0, [], []
# laporan-fix-1 (putusan pemilik 10 Nov): HANYA bagian ekuitas neraca + validasi laba boleh berubah; selain itu lama == baru
DIPUTUSKAN = (".balance_sheet.ekuitas", ".balance_sheet.total_liabilitas_ekuitas", ".balance_sheet._balanced", ".balance_sheet.basis_applied",
              ".validation_errors")
# laporan-fix-2 (putusan pemilik 10 Nov + arah MASTER 12 Nov): basis kas Laba Rugi berubah (hanya kunci ...|cash), label butir arus kas (liabilitas = nama akun);
# TOTAL arus kas, akrual, dan neraca (di luar ekuitas) tetap wajib sama.
ITEM_ARUS = (".cash_flow.operasi.items", ".cash_flow.investasi.items", ".cash_flow.pendanaan.items")


def boleh(k, j):
    return j.startswith(DIPUTUSKAN + ITEM_ARUS) or (k.endswith("|cash") and j.startswith(".income_statement"))


for k, v in lama["lama"].items():
    a, b = dict(ratakan(v)), dict(ratakan(baru["lama"].get(k, {})))
    beda_kunci = sorted(set(a) ^ set(b))
    if [j for j in beda_kunci if not boleh(k, j)]:
        gagal.append(f"KUNCI beda {k}: {[j for j in beda_kunci if not boleh(k, j)][:6]}")
        continue
    for j, x in a.items():
        if j not in b:  # kunci hanya di lama dan termasuk bagian yang diputuskan
            diputuskan.append(f"{k}{j}: lama {x!r} baru (hilang)")
            continue
        n += 1
        y = b[j]
        if boleh(k, j):
            if x != y:
                diputuskan.append(f"{k}{j}: lama {x!r} baru {y!r}")
            continue
        if isinstance(x, (int, float)) and isinstance(y, (int, float)) and not isinstance(x, bool):
            selisih = abs(float(x) - float(y))
            if selisih >= 0.01:  # syarat MASTER: >= Rp 0,01 = STOP (angka laporan yang pemilik lihat berubah)
                gagal.append(f"STOP NILAI {k}{j}: lama {x} baru {y} (selisih {selisih})")
            elif x != y:
                ekor += 1
                daftar_ekor.append(f"{k}{j}: lama {x!r} baru {y!r} (selisih {selisih:.2e})")
        elif x != y:
            gagal.append(f"BEDA {k}{j}: lama {x!r} baru {y!r}")
gagal += [f"cek {c[0]} :: {c[2]}" for c in baru["cek"] if not c[1]]
gaps = sorted(set(lama.get("gap", {})) | set(baru.get("gap", {})))
print(f"NERACA gap (aset - L+E) per tenant|awal|akhir|basis: lama -> baru; titik banding {len(gaps)}")
for kk in gaps:
    gl, gb = lama.get("gap", {}).get(kk), baru.get("gap", {}).get(kk)
    print(f"  GAP {kk}: {gl} -> {gb}" + ("  <== ditutup" if gl not in (None, "0.00") and gb == "0.00" else ""))
print(f"PERUBAHAN DIPUTUSKAN (ekuitas neraca / validasi): {len(diputuskan)}")
for d in diputuskan[:60]:
    print("  PUTUS", d)
p50 = lambda xs: sorted(xs)[len(xs) // 2] if xs else 0  # noqa: E731
print(f"laporan {len(lama['lama'])}; medan lama {n}; sama (sen) {n - sum(1 for g in gagal if g.startswith(('STOP', 'BEDA')))}/{n}; "
      f"ekor float berbeda (sen sama) {ekor}; cek baru {sum(1 for c in baru['cek'] if c[1])}/{len(baru['cek'])}")
print(f"GET p50 ms: lama {p50(lama['waktu']):.1f} | baru {p50(baru['waktu']):.1f}")
print(f"DAFTAR EKOR FLOAT (setiap selisih < Rp 0,01; laporan|rentang|basis + jalur medan): {len(daftar_ekor)}")
for e in daftar_ekor:
    print("  EKOR", e)
for g in gagal[:60]:
    print("[GAGAL]", g)
sys.exit(1 if gagal else 0)
