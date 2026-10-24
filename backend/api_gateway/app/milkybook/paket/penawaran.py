"""Paket Milkybook: Penawaran (pilot, rancangan §11.1).

TULIS: buat/ubah/batal/jadikan pesanan penawaran, pelanggan baru, barang baru.
BACA: penawaran, pelanggan, barang, pesanan, faktur, ringkasan dashboard.
NAV: kirim, terima, tolak, duplikat, jadikan faktur (tak ada pratinjau BE -> AS-G3).

Skema tool = SUBSET medan Pydantic BE (dikunci tests/milkybook/test_paket_penawaran.py, tes teks-sumber):
  CreateQuoteRequest / UpdateQuoteRequest / QuoteItemCreate  schemas/quotes.py
  VoidQuoteRequest / ConvertToOrderRequest                   schemas/quotes.py
  CreateCustomerRequest                                      schemas/customers.py   (TANPA ar_opening_balance: jurnal)
  CreateItemRequest                                          schemas/items.py       (TANPA opening_stock*: jurnal)

Gerbang T0 (putusan MASTER 24 Okt): WRITE tanpa idempotency `butuh` AS-G1 (ubah/batal/jadikan pesanan
Penawaran) atau AS-G14 (pratinjau + idempotency buat pelanggan/barang). Sebelum mendarat -> NAV otomatis.
Buat penawaran TIDAK butuh: sudah idempoten (idem_buat QUOTE_CREATE, quotes.py:691, diukur c8c24092).
Jadikan pesanan TIDAK butuh: idempoten alami (satu penawaran = paling banyak satu SO; ulang = SO yang sama).
Rute pratinjau pelanggan/barang (`/api/customers/preview`, `/api/items/preview`) BELUM ADA di BE
c8c24092: kontrak yang DIASUMSIKAN untuk T0, lihat tests/milkybook/eval/stub_be.py.
"""
from __future__ import annotations

import os

from ..inti.registri import Kelas, Paket, Tool

AS_G1 = "AS-G1"
AS_G14 = "AS-G14"


def _obj(props: dict, wajib=()) -> dict:
    return {"type": "object", "properties": props, "required": list(wajib), "additionalProperties": False}


S = lambda d="": {"type": "string", "description": d} if d else {"type": "string"}  # noqa: E731
N = lambda d="": {"type": "number", "description": d} if d else {"type": "number"}  # noqa: E731
I = lambda d="": {"type": "integer", "description": d} if d else {"type": "integer"}  # noqa: E731
B = lambda d="": {"type": "boolean", "description": d} if d else {"type": "boolean"}  # noqa: E731
TGL = {"type": "string", "format": "date", "description": "YYYY-MM-DD"}
ID = {"type": "string", "description": "UUID dari hasil tool baca"}

# ---- baris penawaran (QuoteItemCreate)
_BARIS = _obj({
    "item_id": ID,
    "description": S("Keterangan baris; biasanya nama barang"),
    "quantity": N("Jumlah, > 0"),
    "unit": S("Satuan, mis. pcs"),
    "unit_price": N("Harga satuan rupiah (dari master barang atau ucapan pengguna)"),
    "discount_percent": N("Diskon baris persen 0-100"),
    "tax_id": S("Id kode pajak dari opsi_pajak; kosongkan bila tanpa pajak"),
    "tax_rate": N("Tarif pajak persen dari opsi_pajak; 0 = tanpa pajak"),
}, ["description", "quantity", "unit_price"])

_PENAWARAN_BARU = {
    "quote_date": TGL,
    "expiry_date": TGL,
    "customer_id": ID,
    "customer_name": S("Nama pelanggan persis seperti di master"),
    "subject": S("Perihal"),
    "reference": S("Referensi eksternal"),
    "notes": S("Catatan untuk pelanggan, mis. jadwal kirim"),
    "discount_type": {"type": "string", "enum": ["fixed", "percentage"]},
    "discount_value": N("Diskon dokumen"),
    "items": {"type": "array", "items": _BARIS},
}
SKEMA_PENAWARAN_BARU = _obj(_PENAWARAN_BARU, ["quote_date", "customer_id", "customer_name", "items"])

# ---- ubah (UpdateQuoteRequest + QuoteItemUpdate.id)
_BARIS_UBAH = _obj({**_BARIS["properties"], "id": S("Id baris lama (dari detail) bila mengubah baris yang ada")},
                   ["description", "quantity", "unit_price"])
SKEMA_UBAH = _obj({"id": ID, **{k: v for k, v in _PENAWARAN_BARU.items() if k != "items"},
                   "items": {"type": "array", "items": _BARIS_UBAH}}, ["id"])
SKEMA_BATAL = _obj({"id": ID, "reason": S("Alasan batal")}, ["id"])
SKEMA_JADI_PESANAN = _obj({"id": ID, "order_date": TGL, "expected_ship_date": TGL}, ["id"])
SKEMA_ID = _obj({"id": ID}, ["id"])

# ---- pelanggan baru (CreateCustomerRequest, tanpa saldo awal)
SKEMA_PELANGGAN = _obj({
    "name": S("Nama pelanggan"),
    "customer_type": {"type": "string", "enum": ["BADAN", "ORANG_PRIBADI"], "description": "BADAN = perusahaan"},
    "company_name": S(),
    "contact_person": S(),
    "phone": S(),
    "email": S(),
    "address": S(),
    "city": S(),
    "is_pkp": B("Pengusaha Kena Pajak"),
    "tax_id": S("NPWP"),
    "payment_terms_days": I("Syarat bayar dalam hari; kosongkan untuk bawaan usaha"),
}, ["name"])

# ---- barang baru (CreateItemRequest, tanpa stok awal)
SKEMA_BARANG = _obj({
    "name": S("Nama barang/jasa"),
    "item_type": {"type": "string", "enum": ["goods", "non_inventory", "service"],
                  "description": "goods = stok dilacak; non_inventory = barang tanpa stok; service = jasa"},
    "track_inventory": B("Hanya untuk goods"),
    "bisa_dikirim": B("Hanya non_inventory: barang dikirim lewat Surat Jalan. Jasa selalu false. TANYAKAN ke pengguna."),
    "base_unit": S("Satuan dasar, mis. pcs"),
    "kategori": S(),
    "deskripsi": S(),
    "item_code": S("Kode/SKU"),
    "sales_price": N("Harga jual"),
    "purchase_price": N("Harga beli"),
    "sales_tax_id": S("Id kode pajak penjualan dari opsi_pajak"),
}, ["name", "item_type", "base_unit"])

_Q = "/api/quotes"
_PEN = "/penjualan/penawaran"

TOOLS = [
    # ---------------- BACA
    Tool("cari_pelanggan", Kelas.READ, "Cari pelanggan menurut nama/kode. Pakai sebelum membuat pelanggan baru.",
         _obj({"q": S("Kata kunci"), "limit": I()}, ["q"]), "GET", "/api/customers/autocomplete"),
    Tool("cari_barang", Kelas.READ, "Cari barang/jasa menurut nama atau barcode; hasil memuat harga jual dan pajak bawaan.",
         _obj({"search": S("Kata kunci"), "limit": I()}, ["search"]), "GET", "/api/items"),
    Tool("opsi_pajak", Kelas.READ, "Daftar kode pajak yang sah (id, kode, nama, tarif, bawaan). direction=output untuk penjualan, input untuk pembelian.",
         _obj({"direction": {"type": "string", "enum": ["output", "input"]}}), "GET", "/api/tax-codes/dropdown"),
    Tool("bawaan_penawaran", Kelas.READ, "Bawaan penawaran usaha ini: masa berlaku, syarat, teks pembuka/penutup.",
         _obj({}), "GET", f"{_Q}/defaults"),
    Tool("daftar_penawaran", Kelas.READ, "Daftar penawaran. aksi: perlu_dikirim | tunggu_jawaban | segera_habis | lewat_masa | perlu_dipesan.",
         _obj({"search": S(), "status": S(), "aksi": {"type": "string", "enum": ["perlu_dikirim", "tunggu_jawaban", "segera_habis", "lewat_masa", "perlu_dipesan"]},
               "customer_id": ID, "limit": I()}), "GET", _Q),
    Tool("detail_penawaran", Kelas.READ, "Detail satu penawaran: baris, total, status, dan `aksi` (kunci tindak lanjut dari server: perlu_dikirim | tunggu_jawaban | segera_habis | lewat_masa | perlu_dipesan, atau kosong).",
         SKEMA_ID, "GET", f"{_Q}/{{id}}"),
    Tool("ringkasan_penawaran", Kelas.READ, "Ringkasan jumlah dan nilai penawaran per status.", _obj({}), "GET", f"{_Q}/summary"),
    Tool("penawaran_segera_kedaluwarsa", Kelas.READ, "Penawaran yang akan segera kedaluwarsa.", _obj({}), "GET", f"{_Q}/expiring"),
    Tool("daftar_pesanan", Kelas.READ, "Daftar pesanan penjualan (SO).", _obj({"search": S(), "status": S(), "limit": I()}), "GET", "/api/sales-orders"),
    Tool("daftar_faktur", Kelas.READ, "Daftar faktur penjualan.", _obj({"search": S(), "status": S(), "limit": I()}), "GET", "/api/sales-invoices"),
    Tool("ringkasan_usaha", Kelas.READ, "Ringkasan dashboard usaha (penjualan, piutang, kas) dari server.", _obj({}), "GET", "/api/dashboard/v2/summary"),

    # ---------------- PRATINJAU (nol tulis)
    Tool("hitung_penawaran", Kelas.PREVIEW, "Hitung subtotal, pajak, dan total penawaran tanpa menyimpan.",
         SKEMA_PENAWARAN_BARU, "POST", f"{_Q}/calculate"),
    Tool("pratinjau_ubah_penawaran", Kelas.PREVIEW, "Pratinjau perubahan penawaran tanpa menyimpan.",
         SKEMA_UBAH, "POST", f"{_Q}/{{id}}/edit/preview"),
    Tool("pratinjau_batal_penawaran", Kelas.PREVIEW, "Pratinjau pembatalan penawaran: boleh atau tidak, dan alasannya.",
         SKEMA_BATAL, "POST", f"{_Q}/{{id}}/void/preview"),
    Tool("pratinjau_jadi_pesanan", Kelas.PREVIEW, "Pratinjau penawaran dijadikan pesanan penjualan.",
         SKEMA_JADI_PESANAN, "POST", f"{_Q}/{{id}}/to-order/preview"),
    Tool("pratinjau_pelanggan_baru", Kelas.PREVIEW, "Pratinjau pelanggan baru (validasi, kembar nama).",
         SKEMA_PELANGGAN, "POST", "/api/customers/preview", butuh=(AS_G14,)),
    Tool("pratinjau_barang_baru", Kelas.PREVIEW, "Pratinjau barang baru (validasi, kembar nama).",
         SKEMA_BARANG, "POST", "/api/items/preview", butuh=(AS_G14,)),

    # ---------------- TULIS (hanya lewat kartu Tinjau)
    Tool("buat_penawaran", Kelas.WRITE, "Usulkan penawaran baru (draf). Aplikasi menampilkan kartu Tinjau; tersimpan sesudah Setuju.",
         SKEMA_PENAWARAN_BARU, "POST", _Q, pratinjau="hitung_penawaran",  # idempoten sejak 4 Okt: idem_buat QUOTE_CREATE quotes.py:691
         halaman=f"{_PEN}/baru", judul_kartu="Buat penawaran"),
    Tool("ubah_penawaran", Kelas.WRITE, "Usulkan perubahan penawaran. Kartu Tinjau; tersimpan sesudah Setuju.",
         SKEMA_UBAH, "PATCH", f"{_Q}/{{id}}", pratinjau="pratinjau_ubah_penawaran", butuh=(AS_G1,),
         halaman=f"{_PEN}/{{id}}", judul_kartu="Ubah penawaran"),
    Tool("batalkan_penawaran", Kelas.WRITE, "Usulkan pembatalan penawaran. Kartu Tinjau; batal sesudah Setuju.",
         SKEMA_BATAL, "POST", f"{_Q}/{{id}}/void", pratinjau="pratinjau_batal_penawaran", butuh=(AS_G1,),
         halaman=f"{_PEN}/{{id}}", judul_kartu="Batalkan penawaran"),
    Tool("jadikan_pesanan", Kelas.WRITE, "Usulkan penawaran dijadikan pesanan penjualan. Kartu Tinjau; terjadi sesudah Setuju.",
         SKEMA_JADI_PESANAN, "POST", f"{_Q}/{{id}}/to-order", pratinjau="pratinjau_jadi_pesanan",
         # idempoten alami: kunci QUOTE + FOR UPDATE, ulang = SO yang sama (quotes.py _konversi_penawaran :2160)
         halaman=f"{_PEN}/{{id}}", judul_kartu="Jadikan pesanan"),
    Tool("buat_pelanggan", Kelas.WRITE, "Usulkan pelanggan baru (tanpa saldo awal). Kartu Tinjau; tersimpan sesudah Setuju.",
         SKEMA_PELANGGAN, "POST", "/api/customers", pratinjau="pratinjau_pelanggan_baru", butuh=(AS_G14,),
         halaman="/kontak/pelanggan", judul_kartu="Buat pelanggan"),
    Tool("buat_barang", Kelas.WRITE, "Usulkan barang/jasa baru (tanpa stok awal). Kartu Tinjau; tersimpan sesudah Setuju.",
         SKEMA_BARANG, "POST", "/api/items", pratinjau="pratinjau_barang_baru", butuh=(AS_G14,),
         halaman="/persediaan/barang", judul_kartu="Buat barang"),

    # ---------------- NAV (izin aksi diperiksa, rute tidak dipanggil)
    Tool("buka_kirim_penawaran", Kelas.NAV, "Buka halaman penawaran untuk mengirimnya ke pelanggan.",
         SKEMA_ID, "POST", f"{_Q}/{{id}}/send", halaman=f"{_PEN}/{{id}}"),
    Tool("buka_terima_penawaran", Kelas.NAV, "Buka halaman penawaran untuk mencatat diterima pelanggan.",
         SKEMA_ID, "POST", f"{_Q}/{{id}}/accept", halaman=f"{_PEN}/{{id}}"),
    Tool("buka_tolak_penawaran", Kelas.NAV, "Buka halaman penawaran untuk mencatat ditolak pelanggan.",
         SKEMA_ID, "POST", f"{_Q}/{{id}}/decline", halaman=f"{_PEN}/{{id}}"),
    Tool("buka_duplikat_penawaran", Kelas.NAV, "Buka halaman penawaran untuk menduplikatnya.",
         SKEMA_ID, "POST", f"{_Q}/{{id}}/duplicate", halaman=f"{_PEN}/{{id}}"),
    Tool("buka_jadikan_faktur", Kelas.NAV, "Buka halaman penawaran untuk menjadikannya faktur.",
         SKEMA_ID, "POST", f"{_Q}/{{id}}/to-invoice", halaman=f"{_PEN}/{{id}}"),
]

_BERKAS_PENGETAHUAN = os.path.join(os.path.dirname(__file__), "..", "pengetahuan", "penawaran.md")


def paket() -> Paket:
    with open(_BERKAS_PENGETAHUAN, encoding="utf-8") as f:
        return Paket("penawaran", TOOLS, f.read())
