#!/usr/bin/env python3
"""Gerbang ASAP pasca-deploy: PANGGIL endpoint yang dipakai setiap hari.

KENAPA ADA. Sesudah deploy a3931d80 seluruh gerbangku HIJAU -- gate_uang,
gate_tpl, gate_so_po -- sementara GET /api/sales-invoices/{id} mati 500 untuk
SEMUA faktur. Owner tak bisa membuka detail faktur mana pun. Tak satu pun
gerbang menyentuhnya karena semuanya bekerja di lapis LAYANAN: mereka
memanggil fungsi render dan skema, tidak pernah menembak rutenya. Dan
`py_compile` tak menangkap NameError di cabang yang tak dieksekusi.

Yang menangkap bug itu regresi E2E sesi lain, bukan gerbangku. Ini menutupnya:
satu permintaan HTTP nyata per endpoint utama, menuntut 200 (atau 403 yang
memang DIHARAPKAN karena peran akun uji). Bukan healthz -- healthz 200
sementara setiap detail faktur 500.

Pakai: gate_asap.py <basis-url>
"""
import json
import os
import sys
import urllib.error
import urllib.request

BASIS = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8001"
gagal = []


def minta(metode, jalur, token=None, data=None):
    req = urllib.request.Request(BASIS + jalur, method=metode)
    if token:
        req.add_header("Authorization", "Bearer " + token)
    if data is not None:
        req.add_header("Content-Type", "application/json")
        data = json.dumps(data).encode()
    try:
        with urllib.request.urlopen(req, data, timeout=20) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except Exception:
            return e.code, {}
    except Exception as e:
        return 0, {"error": str(e)}


def cek(kode, harap, label):
    ok = kode in harap
    print(f"  {'OK  ' if ok else 'GAGAL'} {label:44} -> {kode} (harap {harap})")
    if not ok:
        gagal.append(f"{label} -> {kode}")


def utama():
    print(f"basis: {BASIS}")
    kode, d = minta("POST", "/api/auth/login", data={
        "email": os.environ["MH_UJI_EMAIL"], "password": os.environ["MH_UJI_SANDI"]})
    token = ((d.get("data") or {}).get("access_token")) if kode == 200 else None
    cek(kode, {200}, "POST /api/auth/login")
    if not token:
        print("tak ada token — sisa pemeriksaan dilewati")
        return 1

    kode, inv = minta("GET", "/api/sales-invoices?limit=1", token)
    cek(kode, {200}, "GET  /api/sales-invoices (daftar)")
    items = (inv.get("data") or inv).get("items") if isinstance(inv, dict) else None
    inv_id = items[0]["id"] if items else None

    if inv_id:
        # INI yang mati 500 dan tak tertangkap gerbang mana pun.
        kode, _ = minta("GET", f"/api/sales-invoices/{inv_id}", token)
        cek(kode, {200}, "GET  /api/sales-invoices/{id} (DETAIL)")
    else:
        cek(0, {200}, "GET  /api/sales-invoices/{id} — tak ada faktur untuk diuji")

    # 403 DIHARAPKAN untuk dua rute ini: akun uji berperan Collaborator dan
    # memang tak punya izin baca Pesanan/Penawaran. Membiarkannya merah
    # selamanya membuat gerbang ini diabaikan -- kelas yang sama dengan
    # gerbang yang selalu hijau. Tapi 500 di situ TETAP tertangkap, karena
    # yang diterima hanya {200, 403}, bukan "apa saja".
    for jalur, label, harap in (
        ("/api/sales-orders?limit=1", "GET  /api/sales-orders", {200, 403}),
        ("/api/quotes?limit=1", "GET  /api/quotes", {200, 403}),
        ("/api/customers?limit=1", "GET  /api/customers", {200}),
        ("/api/items?limit=1", "GET  /api/items", {200}),
        ("/api/bank-accounts", "GET  /api/bank-accounts", {200}),
        ("/api/tenant/profile", "GET  /api/tenant/profile", {200}),
    ):
        kode, _ = minta("GET", jalur, token)
        cek(kode, harap, label)

    print()
    if gagal:
        print("GAGAL:")
        for g in gagal:
            print("  - " + g)
        return 1
    print("HIJAU: seluruh endkontak utama menjawab 200.")
    return 0


if __name__ == "__main__":
    sys.exit(utama())
