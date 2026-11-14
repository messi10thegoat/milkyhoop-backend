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
    # Biaya B7 (gelombang 1): pratinjau hapus draf -> DELETE /expenses/{id} (penentu SAMA: biaya_daftar.blok_hapus; izin expense D)
    "POST /api/expenses/{expense_id}/delete/preview": "DELETE /api/expenses/{expense_id}",
    # Biaya B2 (gelombang 2): pratinjau ubah draf -> PATCH /expenses/{id} (penentu SAMA: ubah_biaya_inti; izin expense U); pratinjau buat -> POST /api/expenses (default)
    "POST /api/expenses/{expense_id}/edit/preview": "PATCH /api/expenses/{expense_id}",
    # pelunasan faktur/pesanan menulis receive_payments (penulis bersama so_pelunasan._jalankan_inti)
    "POST /api/sales-invoices/{invoice_id}/receive-payment/preview": "POST /api/receive-payments",
    "POST /api/sales-orders/{order_id}/receive-payment/preview": "POST /api/receive-payments",
    # V359 kode order: pratinjau setelan -> PUT setelan (PEMILIK; izin rute tenant_settings U)
    "POST /api/settings/order-codes/preview": "PUT /api/settings/order-codes",
    # ubah SO terkonfirmasi: pratinjau menjalankan penulis PATCH /sales-orders/{id} (so_ubah_terkonfirmasi.ubah)
    "POST /api/sales-orders/{order_id}/edit/preview": "PATCH /api/sales-orders/{order_id}",
    "POST /api/quotes/{quote_id}/edit/preview": "PATCH /api/quotes/{quote_id}",
    # AS-G4 (24 Okt): ubah serah-terima SJ = PUT (penentu bersama deliveries._rencana_serah)
    "POST /api/deliveries/{delivery_id}/serah/preview": "PUT /api/deliveries/{delivery_id}/serah",
    # U2b ubah draf proforma: pratinjau menjalankan penulis PATCH /proformas/{id} (proformas._tulis_ubah)
    "POST /api/proformas/{proforma_id}/update/preview": "PATCH /api/proformas/{proforma_id}",
    # kontrak PO tahap 2: ubah = PATCH, hapus = DELETE (penentu bersama services/po_rencana)
    "POST /api/purchase-orders/{po_id}/edit/preview": "PATCH /api/purchase-orders/{po_id}",
    # kelola rekening Kas & Bank (rek-r2): pratinjau hapus menjalankan penentu yang sama dengan DELETE /bank-accounts/{id}
    "POST /api/bank-accounts/{bank_account_id}/delete/preview": "DELETE /api/bank-accounts/{bank_account_id}",
    "POST /api/purchase-orders/{po_id}/delete/preview": "DELETE /api/purchase-orders/{po_id}",
    # CW penuh UM Pembelian: buat = POST koleksi, hapus = DELETE (penentu bersama services/uang_muka_vendor_rencana)
    "POST /api/vendor-deposits/preview": "POST /api/vendor-deposits",
    "POST /api/vendor-deposits/{deposit_id}/delete/preview": "DELETE /api/vendor-deposits/{deposit_id}",
    # AS-GP4 (24 Okt): ubah UM = PATCH (penentu bersama R.rencana_ubah)
    "POST /api/vendor-deposits/{deposit_id}/edit/preview": "PATCH /api/vendor-deposits/{deposit_id}",
    "POST /api/customer-deposits/{deposit_id}/edit/preview": "PATCH /api/customer-deposits/{deposit_id}",  # AS-G10
    # Faktur Pembelian tahap 2: ubah draf = PATCH /bills/v2/{id} (penentu bersama services/tagihan_rencana.blok_ubah + hitung)
    "POST /api/bills/{bill_id}/edit/preview": "PATCH /api/bills/v2/{bill_id}",
    "POST /api/bills/{bill_id}/delete/preview": "DELETE /api/bills/{bill_id}",
    "POST /api/receive-payments/{payment_id}/delete/preview": "DELETE /api/receive-payments/{payment_id}",  # U-P4b-2
    # AS-GP3 Pembayaran Keluar: hapus draf = DELETE (penentu bersama services/bayar_keluar_rencana.rencana_hapus_draf_bayar)
    "POST /api/bill-payments/{payment_id}/delete/preview": "DELETE /api/bill-payments/{payment_id}",
    "POST /api/quotes/{quote_id}/delete/preview": "DELETE /api/quotes/{quote_id}",  # QA-AGEN 25 Okt
    # Barang P3 (13 Nov): ubah = PUT, hapus = DELETE, status = PATCH, hapus kategori = DELETE (penentu bersama services/barang_aksi + items._periksa_ubah)
    "POST /api/items/{item_id}/edit/preview": "PUT /api/items/{item_id}",
    "POST /api/items/{item_id}/delete/preview": "DELETE /api/items/{item_id}",
    "POST /api/items/{item_id}/status/preview": "PATCH /api/items/{item_id}/status",
    "POST /api/items/categories/delete/preview": "DELETE /api/items/categories",
    # daftar harga (ANDROID U3, 4 Nov): ubah baris = PATCH (penentu bersama price_lists.siapkan_baris_harga)
    # daftar harga D2 (13 Nov): hapus daftar = DELETE, ubah header = PATCH, hapus baris = DELETE (penentu bersama services/harga_aksi)
    "POST /api/price-lists/{price_list_id}/delete/preview": "DELETE /api/price-lists/{price_list_id}",
    "POST /api/price-lists/{price_list_id}/edit/preview": "PATCH /api/price-lists/{price_list_id}",
    "POST /api/price-lists/{price_list_id}/items/{line_id}/delete/preview": "DELETE /api/price-lists/{price_list_id}/items/{item_id}",
    "POST /api/price-lists/{price_list_id}/items/{line_id}/edit/preview": "PATCH /api/price-lists/{price_list_id}/items/{line_id}",
    # KONTAK standar SO (9 Nov): ubah = PATCH, hapus = DELETE (penentu bersama _siapkan_ubah_* / smart_delete.cek_jejak)
    "POST /api/customers/{customer_id}/edit/preview": "PATCH /api/customers/{customer_id}",
    "POST /api/customers/{customer_id}/delete/preview": "DELETE /api/customers/{customer_id}",
    "POST /api/vendors/{vendor_id}/edit/preview": "PATCH /api/vendors/{vendor_id}",
    "POST /api/vendors/{vendor_id}/delete/preview": "DELETE /api/vendors/{vendor_id}",
    # Penyesuaian stok standar SO (9 Nov): ubah draf = PATCH, hapus draf = DELETE (penentu bersama services/stok_sesuai)
    "POST /api/stock-adjustments/{adjustment_id}/edit/preview": "PATCH /api/stock-adjustments/{adjustment_id}",
    "POST /api/stock-adjustments/{adjustment_id}/delete/preview": "DELETE /api/stock-adjustments/{adjustment_id}",
    "POST /api/customers/{customer_id}/status/preview": "PATCH /api/customers/{customer_id}/status",
    "POST /api/vendors/{vendor_id}/status/preview": "PATCH /api/vendors/{vendor_id}/status",
}
# pratinjau yang SENGAJA beda izin, dengan sebab tertulis (hanya boleh menyusut)
BEDA_SENGAJA = {
    # (kosong) K-D 9 Nov 2026: /customers/merge/preview kini = izin /merge (C), simetri vendor
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
