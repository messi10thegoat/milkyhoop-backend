"""PENJAGA — rute PRATINJAU wajib ber-izin SAMA dengan rute TULIS-nya (1 Okt 2026).

KENAPA ADA: 30 Sep ADMIN grapgrap ditolak 403 PERMISSION_UNMAPPED ("Aksi ini belum diberi izin
untuk peran Anda") di halaman Pembayaran CW. Penyebab: POST /sales-invoices/{id}/receive-payment/
preview tak punya pola di ROUTE_PERMISSIONS -> tulis tak-terpetakan = tertutup untuk semua kecuali
OWNER. Peran ADMIN SUDAH punya RECEIPT C; yang hilang hanya petanya. Rute PRATINJAU yang lain
(kirim SO) jatuh ke prefiks ^/api/sales-orders = sales_order C, padahal tulisnya sales_invoice P:
pratinjau bilang "boleh", tulis bilang 403 (atau sebaliknya).

Aturan: pratinjau menjalankan penulis yang SAMA lalu rollback, jadi izinnya = izin tulisnya.
Tak lebih longgar (bocor aturan/angka), tak lebih ketat (layar mati padahal tulis boleh).

Pasangan diturunkan dari inventaris (`.../preview` -> jalur tanpa `/preview`); bila tulisnya
tak ada di jalur itu, WAJIB tercatat di KHUSUS -- rute pratinjau baru tanpa pasangan = MERAH.
Membaca berkas, tanpa impor aplikasi (suite unit = nol DB/HTTP), sama seperti test_pagar_rute_izin.
"""
import re
from pathlib import Path

DISINI = Path(__file__).parent
MIDDLEWARE = DISINI.parents[1] / "app" / "middleware" / "permission_middleware.py"
INVENTARIS = DISINI / "inventaris_rute_tulis.txt"

# pratinjau -> rute tulis yang benar-benar dijalankan, bila bukan "jalur tanpa /preview"
KHUSUS = {
    # pelunasan faktur/pesanan menulis receive_payments (penulis bersama so_pelunasan._jalankan_inti)
    "POST /api/sales-invoices/{invoice_id}/receive-payment/preview": "POST /api/receive-payments",
    "POST /api/sales-orders/{order_id}/receive-payment/preview": "POST /api/receive-payments",
    # V359 kode order: pratinjau setelan -> PUT setelan (PEMILIK; izin rute tenant_settings U)
    "POST /api/settings/order-codes/preview": "PUT /api/settings/order-codes",
    # ubah SO terkonfirmasi: pratinjau menjalankan penulis PATCH /sales-orders/{id} (so_ubah_terkonfirmasi.ubah)
    "POST /api/sales-orders/{order_id}/edit/preview": "PATCH /api/sales-orders/{order_id}",
    # U2b ubah draf proforma: pratinjau menjalankan penulis PATCH /proformas/{id} (proformas._tulis_ubah)
    "POST /api/proformas/{proforma_id}/update/preview": "PATCH /api/proformas/{proforma_id}",
}
# pratinjau yang SENGAJA beda izin, dengan sebab tertulis (hanya boleh menyusut)
BEDA_SENGAJA = {
    # pra-CW (Q customers merge): CACAH baris per tabel, tak menjalankan penulis -> baca (R) disengaja
    "POST /api/customers/merge/preview": ("customer", "R"),
}
# rute tulis yang menjalankan penulis rute lain -> izinnya wajib sama
SAMA_IZIN = [
    # Kirim barang SO = _execute_fulfillment per faktur
    ("POST /api/sales-orders/{order_id}/fulfill", "POST /api/sales-invoices/{invoice_id}/fulfill"),
    # setelan akuntansi: rute lama (deprecated) = rute FE -> hanya pemilik (ADMIN SETTINGS {R}, V353; 1 Okt 2026)
    ("PATCH /api/reports/accounting-settings", "PATCH /api/settings/accounting"),
]


def _baris(p):
    return [b.strip() for b in p.read_text(encoding="utf-8").splitlines()
            if b.strip() and not b.lstrip().startswith("#")]


def _pola():
    teks = MIDDLEWARE.read_text(encoding="utf-8")
    blok = re.search(r"ROUTE_PERMISSIONS[^=]*=\s*\[(.*?)\n\]", teks, re.S).group(1)
    mentah = re.findall(r'\(r"([^"]+)",\s*\[([^\]]*)\],\s*"([^"]+)",\s*"([^"]+)"\)', blok)
    return [(re.compile(p), [x.strip().strip('"').strip("'") for x in m.split(",")], mod, aksi)
            for p, m, mod, aksi in mentah]


POLA = _pola()


def izin(rute):
    """Pola PERTAMA yang cocok -- sama dengan PermissionMiddleware._find_permission."""
    metode, _, jalur = rute.partition(" ")
    k = re.sub(r"\{[^}]+\}", "XX", jalur)
    for rx, mth, mod, aksi in POLA:
        if metode in mth and rx.match(k):
            return (mod, aksi)
    return None


def _pratinjau():
    return [b for b in _baris(INVENTARIS) if b.endswith("/preview")]


def test_tiap_pratinjau_izinnya_sama_dengan_tulisnya():
    inv = set(_baris(INVENTARIS))
    salah = []
    for p in _pratinjau():
        if p in BEDA_SENGAJA:
            if izin(p) != BEDA_SENGAJA[p]:
                salah.append(f"{p} -> {izin(p)}, tercatat BEDA_SENGAJA {BEDA_SENGAJA[p]}")
            continue
        tulis = KHUSUS.get(p) or p[: -len("/preview")]
        if tulis not in inv:
            salah.append(f"{p}: rute tulis '{tulis}' tak ada -- catat pasangannya di KHUSUS")
        elif izin(p) is None or izin(p) != izin(tulis):
            salah.append(f"{p} -> {izin(p)}  !=  {tulis} -> {izin(tulis)}")
    assert not salah, "Pratinjau ber-izin beda dari tulisnya:\n  " + "\n  ".join(salah)


def test_tulis_berpenulis_sama_izinnya_sama():
    for a, b in SAMA_IZIN:
        assert izin(a) is not None and izin(a) == izin(b), f"{a} -> {izin(a)} != {b} -> {izin(b)}"


def test_khusus_tak_basi():
    """Entri KHUSUS untuk pratinjau yang sudah tak ada = penjaga diam-diam menyusut."""
    inv = set(_baris(INVENTARIS))
    basi = [k for k in list(KHUSUS) + list(BEDA_SENGAJA) if k not in inv] + [v for v in KHUSUS.values() if v not in inv]
    assert not basi, basi


def test_kontrol_positif():
    """Nol pratinjau terbaca = tes di atas hijau selamanya."""
    assert len(_pratinjau()) >= 10, _pratinjau()
    assert izin("POST /api/sales-invoices/{invoice_id}/void") == ("sales_invoice", "V")
