#!/usr/bin/env python3
"""GATE T145 -- label "Terdeteksi dari" pada kartu create_item.

READ ONLY. Nol tulisan DB, nol impor modul aplikasi. Gate ini MENGEKSEKUSI
POTONGAN BYTE SUMBER ASLI dari tool_executor.py (bukan salinan logika), jadi ia
tak bisa hijau gara-gara reimplementasi yang keliru.

Pakai:
    python gate_t145.py [PATH_tool_executor.py]
Default: /app/backend/api_gateway/app/services/unified_agent/tool_executor.py
(jalur DI DALAM kontainer milkyhoop-dev-api_gateway).

Kelas uji:
  M1     MERAH sebelum perbaikan, HIJAU sesudah -- insiden nyata
         (pending_action 93198afc-adf9-4618-b0d5-d2ab6318e4bf): kunci "name"
         membawa nama LENGKAP, kunci "item_name" membawa varian TERPOTONG.
  K1-K3  kontrol positif/negatif -- membuktikan assertion M1 BISA lulus DAN
         BISA gagal (uji yang tak bisa gagal bukan uji).
  H1-H3  HIJAU di KEDUA sisi -- rekonstruksi uji party-order yang DIKLAIM oleh
         commit ea804bcb ("3/3") tapi TAK PERNAH dikomit ke repo.
         Kalau salah satu H jatuh, perbaikannya yang salah, bukan gate-nya.
"""

import sys
import textwrap

SRC = (
    sys.argv[1]
    if len(sys.argv) > 1
    else "/app/backend/api_gateway/app/services/unified_agent/tool_executor.py"
)
lines = open(SRC, encoding="utf-8").read().splitlines(True)

# Blok pembangun _det_parts .. _detection_reason (1-indexed, inklusif).
START, END = 1932, 2019
blob = "".join(lines[START - 1 : END])

# --- ASSERT STRUKTURAL: mematikan gate kalau nomor baris bergeser ---
# Tanpa ini, START/END yang meleset akan diam-diam mengeksekusi blok yang salah
# (atau blok tanpa pencetak) dan seluruh gate jadi tak bermakna.
assert blob.lstrip().startswith("_det_parts = []"), "START meleset: " + repr(blob[:80])
assert "FIX_DETECTION_PARTY_ITEM" in blob, "cabang item tidak ikut terpotong"
assert '("item_name", "barang")' in blob, "kunci item_name tidak ikut terpotong"
assert (
    "for _det_key, _det_label in _det_party_order:" in blob
), "pencetak tidak ikut terpotong"
assert "_detection_reason = (" in blob, "END meleset: _detection_reason tak ada"
CODE = compile(textwrap.dedent(blob), SRC, "exec")


def label(action_key, payload):
    g = {"action_key": action_key, "payload": payload}
    exec(CODE, g)
    return g["_detection_reason"]


NAMA_LENGKAP = "Kaos 20s + Sablon Plastisol (Size XS-XL)"
NAMA_POTONG = "Kaos 20s + Sablon Plastisol"

hasil = []


def cek(nama, kelas, ok, detail):
    hasil.append((nama, kelas, ok, detail))


# ---------- M1: insiden nyata (MERAH sebelum, HIJAU sesudah) ----------
lab = label("create_item", {"item_name": NAMA_POTONG, "name": NAMA_LENGKAP})
cek(
    "M1 insiden nyata: label memuat nama LENGKAP",
    "MERAH-sebelum / HIJAU-sesudah",
    NAMA_LENGKAP in lab,
    lab,
)

# ---------- KONTROL POSITIF: gate BISA HIJAU ----------
lab = label("create_item", {"name": NAMA_LENGKAP})  # item_name absen
cek("K1 kontrol positif (item_name absen)", "harus HIJAU", NAMA_LENGKAP in lab, lab)

lab = label("create_item", {"item_name": NAMA_LENGKAP, "name": NAMA_LENGKAP})
cek("K2 kontrol positif (kedua field lengkap)", "harus HIJAU", NAMA_LENGKAP in lab, lab)

# ---------- KONTROL NEGATIF: assertion BISA GAGAL ----------
lab = label("create_item", {"item_name": "XXX", "name": "XXX"})
cek("K3 kontrol negatif (nol nama benar)", "harus MERAH", NAMA_LENGKAP not in lab, lab)

# ---------- H1-H3: party-order, HIJAU di KEDUA sisi ----------
lab = label(
    "create_item",
    {
        "item_name": "Kaos Polos",
        "vendor_name": "NONENG",
        "customer_name": "Toko Melati",
    },
)
cek(
    "H1 create_item: vendor/customer TIDAK bocor",
    "harus HIJAU",
    "NONENG" not in lab and "Toko Melati" not in lab and "barang 'Kaos Polos'" in lab,
    lab,
)

lab = label(
    "create_vendor",
    {"vendor_name": "NONENG", "customer_name": "Toko Melati", "name": "NONENG"},
)
cek(
    "H2 create_vendor: customer TIDAK bocor",
    "harus HIJAU",
    "Toko Melati" not in lab and "vendor 'NONENG'" in lab,
    lab,
)

lab = label(
    "create_receive_payment",
    {"customer_name": "Aqua", "vendor_name": "Knitto Textile Holis", "amount": 500000},
)
cek(
    "H3 receive_payment: pelanggan menang atas vendor basi",
    "harus HIJAU",
    "Knitto" not in lab and "pelanggan 'Aqua'" in lab,
    lab,
)

# ---------- Diagnostik ----------
_p = {"item_name": NAMA_POTONG, "name": NAMA_LENGKAP}
print("SUMBER                  :", SRC)
print("DIAGNOSTIK create_item  :", repr(label("create_item", dict(_p))))
print("DIAGNOSTIK create_bill  :", repr(label("create_bill", dict(_p))))
print()
gagal = 0
for _nama, _kelas, _ok, _detail in hasil:
    if not _ok:
        gagal += 1
    _tag = "LULUS" if _ok else "GAGAL"
    print("[" + _tag + "] " + _nama + "  (" + _kelas + ")")
    print("        -> " + repr(_detail))
print("\nRINGKAS: %d/%d lulus, %d gagal" % (len(hasil) - gagal, len(hasil), gagal))
sys.exit(1 if gagal else 0)
