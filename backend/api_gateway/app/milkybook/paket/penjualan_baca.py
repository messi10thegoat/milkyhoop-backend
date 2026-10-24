"""Paket Milkybook: baca modul Penjualan lain (SO, faktur, penerimaan, uang muka, pengiriman, ringkasan).

Pilot = BACA saja (rancangan §11.1). Tulis modul ini = paket sendiri kelak (T5).
Kartu pengetahuan: pengetahuan/penjualan_baca.md (dari kartu agen laporan5/6, sampel diverifikasi ke kode 24 Okt).
Parameter dan enum dikunci tests/milkybook/test_paket_penawaran.py (tes teks-sumber router).
"""
from __future__ import annotations

import os

from ..inti.registri import Kelas, Paket, Tool
from .penawaran import ID, TGL, I, S, _obj

TOOLS = [
    Tool("daftar_pesanan", Kelas.READ, "Daftar pesanan penjualan (SO). aksi = kunci tindak lanjut server.",
         _obj({"search": S(), "status": S(), "customer_id": ID, "limit": I(),
               "aksi": {"type": "string", "enum": ["telat_kirim", "perlu_minta_dp", "menunggu_pembayaran", "perlu_dikonfirmasi",
                                                   "perlu_dikirim", "siap_diserahkan", "perlu_ditagih"]}}), "GET", "/api/sales-orders"),
    Tool("daftar_faktur", Kelas.READ, "Daftar faktur penjualan. status: draft|posted|partial|paid|overdue|void|unpaid|active|all; aksi = kunci tindak lanjut server.",
         _obj({"search": S(), "status": S(), "customer_id": ID, "limit": I(),
               "aksi": {"type": "string", "enum": ["perlu_diterbitkan", "belum_lunas", "segera_tempo", "jatuh_tempo",
                                                   "jatuh_tempo_minggu_ini"]}}), "GET", "/api/sales-invoices"),
    Tool("ringkasan_faktur", Kelas.READ, "Ringkasan faktur penjualan (jumlah dan nilai per status/aksi) dari server.",
         _obj({"search": S()}), "GET", "/api/sales-invoices/summary"),
    Tool("ringkasan_piutang", Kelas.READ, "Ringkasan piutang usaha dari server (sisa tagihan, umur, pelanggan terbesar).",
         _obj({}), "GET", "/api/sales-invoices/outstanding-summary"),
    Tool("daftar_penerimaan", Kelas.READ, "Daftar penerimaan pembayaran pelanggan.",
         _obj({"customer_id": ID, "search": S(), "date_from": TGL, "date_to": TGL, "limit": I(),
               "status": {"type": "string", "enum": ["all", "draft", "posted", "voided"]}}), "GET", "/api/receive-payments"),
    Tool("daftar_uang_muka", Kelas.READ, "Daftar uang muka pelanggan.",
         _obj({"customer_id": ID, "search": S(), "date_from": TGL, "date_to": TGL, "limit": I(),
               "aksi": {"type": "string", "enum": ["belum_diterapkan"]}}), "GET", "/api/customer-deposits"),
    Tool("daftar_pengiriman", Kelas.READ, "Daftar surat jalan (pengiriman barang).",
         _obj({"customer_id": ID, "search": S(), "date_from": TGL, "date_to": TGL, "limit": I(),
               "status": {"type": "string", "enum": ["posted", "voided"]},
               "aksi": {"type": "string", "enum": ["perlu_resi", "belum_dicatat", "perlu_difaktur"]}}), "GET", "/api/deliveries"),
    Tool("ringkasan_usaha", Kelas.READ, "Ringkasan dashboard usaha (penjualan, piutang, kas) dari server.", _obj({}), "GET", "/api/dashboard/v2/summary"),
]

_BERKAS_PENGETAHUAN = os.path.join(os.path.dirname(__file__), "..", "pengetahuan", "penjualan_baca.md")


def paket() -> Paket:
    with open(_BERKAS_PENGETAHUAN, encoding="utf-8") as f:
        return Paket("penjualan_baca", TOOLS, f.read())
