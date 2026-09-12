#!/usr/bin/env python3
"""Sapuan izin DUA ARAH — laporkan saja, jangan ubah apa pun.

ARAH 1: rute TULIS yang tak punya pola  -> lubang TERBUKA
ARAH 2: pola yang tak cocok rute mana pun -> lubang yang TERLIHAT TERTUTUP
        (lebih berbahaya: ia menenangkan pembaca tanpa menjaga apa pun)

Enumerasi dari tabel rute aplikasi yang BERJALAN, bukan dari nama berkas.
"""
import json
import re
import io

RUTE = "/root/rute_hidup.txt"
POLA = "/root/pola_izin.json"

SKIP = [
    r"^/api/auth", r"^/api/health", r"^/api/qr-auth", r"^/api/public",
    r"^/api/docs", r"^/api/openapi", r"^/api/dashboard", r"^/api/permissions",
    r"^/favicon", r"^/$",
]
skip_c = [re.compile(p) for p in SKIP]

# --- muat rute (buang baris log yang mencemari keluaran) -------------------
rute = []
for baris in io.open(RUTE, encoding="utf-8"):
    m = re.match(r"^(GET|POST|PATCH|PUT|DELETE) (/\S*)$", baris.strip())
    if m:
        rute.append((m.group(1), m.group(2)))
rute = sorted(set(rute))

pola = json.load(open(POLA))
pola_c = [(re.compile(p), [x.strip().strip('"').strip("'") for x in mth.split(",")], mod, act)
          for p, mth, mod, act in pola]


def konkret(path):
    """`/api/x/{id}/post` -> `/api/x/XX/post` supaya `[^/]+` bisa mencocokkannya."""
    return re.sub(r"\{[^}]+\}", "XX", path)


def dilewati(path):
    return any(c.match(path) for c in skip_c)


def cocok(method, path):
    p = konkret(path)
    for rx, methods, mod, act in pola_c:
        if rx.match(p) and method in methods:
            return (mod, act)
    return None


TULIS = ("POST", "PATCH", "PUT", "DELETE")

# --- ARAH 1 ----------------------------------------------------------------
terbuka, terjaga, dilewat = [], 0, 0
for m, p in rute:
    if m not in TULIS:
        continue
    if dilewati(p):
        dilewat += 1
        continue
    hit = cocok(m, p)
    if hit:
        terjaga += 1
    else:
        terbuka.append((m, p))

# --- ARAH 2 ----------------------------------------------------------------
rute_konkret = [(m, konkret(p)) for m, p in rute]
mati = []
for (rx, methods, mod, act), asli in zip(pola_c, pola):
    kena = any(rx.match(p) and m in methods for m, p in rute_konkret)
    if not kena:
        mati.append((asli[0], asli[1], mod, act))

# --- peringkat taruhan (NAMA = DUGAAN, wajib diverifikasi) -----------------
JURNAL = ("post", "void", "reverse", "fulfill", "payment", "journal",
          "reconcile", "close", "approve", "settle", "refund")


def taruhan(p):
    ekor = p.rstrip("/").split("/")[-1].lower()
    if any(k in p.lower() for k in JURNAL):
        return "A) DUGA menulis jurnal — WAJIB diverifikasi"
    return "B) menulis data"


print("=" * 72)
print("SAPUAN IZIN — LAPORAN SAJA, NOL PERUBAHAN")
print("=" * 72)
print("rute unik        : %d" % len(rute))
print("rute TULIS       : %d" % sum(1 for m, _ in rute if m in TULIS))
print("  terjaga pola   : %d" % terjaga)
print("  di SKIP_PATTERNS: %d" % dilewat)
print("  TANPA POLA     : %d   <-- ARAH 1" % len(terbuka))
print("pola total       : %d" % len(pola))
print("  tak cocok rute : %d   <-- ARAH 2" % len(mati))
print()

print("-" * 72)
print("ARAH 1 — RUTE TULIS TANPA POLA (lubang terbuka)")
print("-" * 72)
a = [x for x in terbuka if taruhan(x[1]).startswith("A")]
b = [x for x in terbuka if taruhan(x[1]).startswith("B")]
print("\n[A] DUGA MENULIS JURNAL — taruhan tertinggi (%d)" % len(a))
for m, p in a:
    print("   %-6s %s" % (m, p))
print("\n[B] menulis data (%d)" % len(b))
for m, p in b:
    print("   %-6s %s" % (m, p))

print()
print("-" * 72)
print("ARAH 2 — POLA YANG TAK PERNAH COCOK (terlihat tertutup, tak menjaga)")
print("-" * 72)
for p, mth, mod, act in mati:
    print("   %-52s %s %s/%s" % (p, mth, mod, act))
