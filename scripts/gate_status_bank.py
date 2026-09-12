#!/usr/bin/env python3
"""Gerbang: medan `status` pada baris /api/bank-accounts/{id}/transactions.

DUA SISI, data yang SAMA, akun yang SAMA:
  SISI=lama  -> produksi (kode lama): medan `status` ABSEN   = MERAH terbukti
  SISI=baru  -> kontainer worktree  : medan `status` HADIR   = HIJAU

Akun: MH_UJI_* dari /root/mh-akun-uji.env — Collaborator __SYSTEM__, BUKAN Owner.
Login owner memicu register_device/force_logout dan mencabut layar pemilik.

Kontrol positif: kalau nol baris kembali, gerbang menyatakan DIRINYA TAK SAH
alih-alih melaporkan hijau/merah — nol baris membuat kedua sisi terlihat sama.
"""
import json
import os
import sys
import urllib.request

BASIS = os.environ.get("BASIS", "http://127.0.0.1:8001")
SISI = os.environ.get("SISI", "baru")
REK = os.environ["REK_ID"]
UA = "milkyhoop-gate/1.0"


def masuk():
    data = json.dumps(
        {"email": os.environ["MH_UJI_EMAIL"], "password": os.environ["MH_UJI_SANDI"]}
    ).encode()
    req = urllib.request.Request(
        BASIS + "/api/auth/login",
        data=data,
        headers={"Content-Type": "application/json", "User-Agent": UA},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        d = json.loads(r.read())
    return d.get("data", d)["access_token"]


def ambil(tok, qs):
    req = urllib.request.Request(
        f"{BASIS}/api/bank-accounts/{REK}/transactions?{qs}",
        headers={"Authorization": "Bearer " + tok, "User-Agent": UA},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def main():
    tok = masuk()
    d = ambil(tok, "limit=5")
    items = d.get("items", [])

    # --- kontrol positif -------------------------------------------------
    if not items:
        print("GERBANG TAK SAH: nol baris kembali — kedua sisi akan tampak sama.")
        print("   Pakai rekening yang punya transaksi.")
        return 2

    hadir = [("status" in it) for it in items]
    nilai = [it.get("status") for it in items]
    semua_hadir = all(hadir)
    semua_absen = not any(hadir)

    print(f"SISI={SISI}  BASIS={BASIS}  baris={len(items)}")
    print(f"  kunci 'status' hadir di {sum(hadir)}/{len(items)} baris")
    print(f"  nilai: {nilai}")

    if SISI == "lama":
        if semua_absen:
            print("[M] MERAH TERBUKTI: kode lama tak pernah mengirim `status`.")
            print("    -> kendali FE hasPostedSelected selalu undefined.")
            return 0
        print("[!] GAGAL: sisi lama justru MENGIRIM `status` — premisnya keliru.")
        return 1

    if not semua_hadir:
        print("[X] MERAH: medan `status` masih absen di sisi baru.")
        return 1

    if any(v is None for v in nilai):
        print("[X] MERAH: `status` hadir tapi None — kolomnya tak terbaca.")
        return 1

    bukan_besar = [v for v in nilai if v != str(v).upper()]
    if bukan_besar:
        print(f"[X] MERAH: ejaan bukan huruf besar: {bukan_besar}")
        return 1

    print("[H] HIJAU: `status` hadir di SETIAP baris, non-null, HURUF BESAR.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
