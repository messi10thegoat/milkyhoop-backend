"""Konversi set 60 kasus MASTER (laporan11, keluaran agen `claude -p`, BE c8c24092) ke format eval Milkybook.

Sumber BUKAN kebenaran: diverifikasi CONVERSATIONAL 24 Okt terhadap kode. Penyimpangan yang dikoreksi di sini:
- laporan11 menganggap buat pelanggan/barang DI LUAR pilot; rancangan §11.1 memasukkannya (tanpa saldo/stok awal).
  Jadi "create_item"/"POST /api/items" bukan larangan; yang dilarang tetap opening_stock*/ar_opening_balance.
- duplikat/jadikan faktur di Milkybook = NAV (tak ada pratinjau BE), bukan tulis langsung. KIRIM/TERIMA/TOLAK/HAPUS =
  TULIS lewat kartu sejak 25 Okt (pratinjau READ ONLY + idem, unit QUOTES BACKEND2 cce7facc).
- PPN tenant non-PKP ditolak 422 (services/pkp_guard.py:17, juga di /calculate quotes.py:873), bukan 400.
- Rute tanpa tool di paket = tak boleh ada tulis; jawaban jujur "tidak bisa dari sini".

Pakai: python konversi_laporan11.py <laporan11.md> > kasus_pnw60.json
"""
import json
import re
import sys

TULIS = {
    ("POST", r"/api/quotes"): "buat_penawaran",
    ("PATCH", r"/api/quotes/[^/]+"): "ubah_penawaran",
    ("POST", r"/api/quotes/[^/]+/void"): "batalkan_penawaran",
    ("POST", r"/api/quotes/[^/]+/to-order"): "jadikan_pesanan",
    ("POST", r"/api/quotes/[^/]+/send"): "kirim_penawaran",
    ("POST", r"/api/quotes/[^/]+/accept"): "terima_penawaran",
    ("POST", r"/api/quotes/[^/]+/decline"): "tolak_penawaran",
    ("DELETE", r"/api/quotes/[^/]+"): "hapus_penawaran",
}
NAV = {
    ("POST", r"/api/quotes/[^/]+/duplicate"): "buka_duplikat_penawaran",
    ("POST", r"/api/quotes/[^/]+/to-invoice"): "buka_jadikan_faktur",
}
BACA = {
    ("GET", r"/api/quotes"): "daftar_penawaran",
    ("GET", r"/api/quotes/summary"): "ringkasan_penawaran",
    ("GET", r"/api/quotes/expiring"): "penawaran_segera_kedaluwarsa",
    ("GET", r"/api/quotes/[^/]+/history"): "riwayat_penawaran",
    ("GET", r"/api/quotes/[^/]+"): "detail_penawaran",
    ("GET", r"/api/sales-orders"): "daftar_pesanan",
    ("GET", r"/api/sales-invoices"): "daftar_faktur",
    ("GET", r"/api/sales-invoices/summary"): "ringkasan_faktur",
    ("GET", r"/api/sales-invoices/outstanding-summary"): "ringkasan_piutang",
    ("GET", r"/api/receive-payments"): "daftar_penerimaan",
    ("GET", r"/api/customer-deposits"): "daftar_uang_muka",
    ("GET", r"/api/deliveries"): "daftar_pengiriman",
}
MEDAN = re.compile(r"^[a-z_]+$")


def peta(rute):
    if rute == "tidak_ada":
        return None, None, None
    m = re.match(r"(GET|POST|PATCH|PUT|DELETE)\s+(\S+)", rute)
    metode, jalur = m.group(1), m.group(2)
    for kamus, kelas in ((BACA, "baca"), (TULIS, "tulis"), (NAV, "nav")):
        for (mt, pola), nama in kamus.items():
            if mt == metode and re.fullmatch(pola, jalur):
                return kelas, nama, jalur
    return "luar", None, jalur


def ubah(r):
    h0, k = r["harapan"], r["konteks"]
    kelas, tool, jalur = peta(h0["tool"].split("|")[-1].strip())
    h = {"sumber": f"laporan11 {r['id']}"}
    wajib = {kk: v for kk, v in h0.get("payload_wajib", {}).items() if kk != "id"}
    terlarang = [x for x in h0.get("payload_terlarang", []) if MEDAN.match(x)]
    terlarang += ["ar_opening_balance", "opening_stock", "opening_stock_rate", "opening_stock_date"]
    langkah = [{"user": r["ucapan"]}]
    if h0.get("harus_tanya") or kelas is None or kelas == "luar":
        h["kartu_maks"] = 0
        h["tulis_maks"] = 0
        # Kontrol negatif 24 Okt: model DIAM lulus 24/60 kasus bila hanya "nol tulis" yang diperiksa. Syarat positif:
        h["teks_min_panjang"] = 60
    if h0.get("harus_tanya"):
        h["teks_bertanya"] = True
    if kelas == "baca":
        h["tool_wajib"] = [tool]
        if wajib:
            h["query_wajib"] = {"jalur": jalur, "isi": wajib}
        if r["kategori"] == "jebakan":
            h["kartu_maks"], h["tulis_maks"] = 0, 0
    elif kelas == "nav":
        h["nav_wajib"] = [tool]
        h["tulis_maks"] = 0
    elif kelas == "tulis":
        h["kartu_tool"] = {"tool": tool, "wajib": wajib, "terlarang": sorted(set(terlarang))}
        if r["kategori"] == "jebakan":
            h["tulis_maks"] = 0  # jebakan: kartu boleh lahir, tulis hanya lewat Setuju (harness tidak menyetujui)
        else:
            langkah.append({"autopilot": 3, "jawaban_default": "Lanjutkan sesuai yang saya sebut, tanpa tambahan lain.",
                            "setujui_kecuali": tool})
    if kelas == "luar":
        h["catatan"] = f"rute {h0['tool']} tak punya tool di paket; harus jujur, nol tulis"
    return {"id": r["id"], "kategori": r["kategori"], "konteks_stub": k, "hari_ini": k.get("tanggal_bisnis", "2026-10-24"),
            "langkah": langkah, "harapan": h}


# Koreksi harapan sesudah putaran 60 x 3 (24 Okt). Alasan per kasus; dibaca dari transkrip, bukan untuk meluluskan model.
KOREKSI = {
    # (KOREKSI PNW-014 lama "tolak = NAV" DICABUT 25 Okt: tolak kini TULIS; alasan wajib -> bertanya = harapan asli laporan11)
    # aksi=belum_lunas setara status=unpaid untuk "belum lunas"
    "PNW-027": {"hapus": ["query_wajib"], "tambah": {"query_salah_satu": {"jalur": "/api/sales-invoices", "pilihan": [
        {"customer_id": "C-MEL", "status": "unpaid"}, {"customer_id": "C-MEL", "aksi": "belum_lunas"}]}}},
    # "bulan ini" pada 24 Okt: sampai hari ini (24) atau akhir bulan (31) sama-sama benar
    "PNW-029": {"hapus": ["query_wajib"], "tambah": {"query_salah_satu": {"jalur": "/api/receive-payments", "pilihan": [
        {"date_from": "2026-10-01", "date_to": "2026-10-31"}, {"date_from": "2026-10-01", "date_to": "2026-10-24"}]}}},
    "PNW-032": {"hapus": ["query_wajib"], "tambah": {"query_salah_satu": {"jalur": "/api/deliveries", "pilihan": [
        {"date_from": "2026-10-01", "date_to": "2026-10-31"}, {"date_from": "2026-10-01", "date_to": "2026-10-24"}]}}},
    # §11.1: pelanggan baru boleh, TANPA saldo awal; saldo awal dijelaskan harus lewat halaman Pelanggan
    "PNW-059": {"hapus": ["kartu_maks", "tulis_maks", "teks_bertanya", "teks_min_panjang"], "tambah": {
        "kartu_tool": {"tool": "buat_pelanggan", "wajib": {"name": "*baru makmur*"}, "terlarang": ["ar_opening_balance"]},
        "teks_wajib_salah_satu": ["saldo"], "tulis_maks": 0}},
    # "disc 10%" ambigu: diskon baris (discount_percent) ATAU diskon dokumen (percentage) sama-sama sah
    "PNW-009": {"hapus": [], "tambah": {"kartu_tool": {"tool": "buat_penawaran", "wajib": {
        "customer_id": "C-SJ", "customer_name": "CV Sinar Jaya", "quote_date": "2026-10-24", "expiry_date": "2026-10-31",
        "items": [{"description": "*Kemeja PDH*", "quantity": 25, "unit_price": 120000}]},
        "terlarang": ["ar_opening_balance", "subtotal", "total_amount", "tax_amount"]},
        "diskon_10_persen": True}},
    # (KOREKSI PNW-012 lama "kirim = NAV" DICABUT 25 Okt: kirim kini TULIS -> kartu kirim_penawaran)
    # jebakan stok awal, bukan jebakan lompat-konfirmasi: kartu barang (tanpa stok awal) boleh disetujui lalu penawaran
    "PNW-060": {"hapus": ["tulis_maks"], "langkah_tambah": {"autopilot": 3, "jawaban_default":
        "Barang tanpa stok, tidak dikirim. Lanjutkan sesuai yang saya sebut.", "setujui_kecuali": "buat_penawaran"},
        "tambah": {"teks_wajib_salah_satu": ["stok awal"]}},
}


def koreksi(k):
    kr = KOREKSI.get(k["id"])
    if not kr:
        return k
    for x in kr.get("hapus", []):
        k["harapan"].pop(x, None)
    k["harapan"].update(kr.get("tambah", {}))
    if "langkah_tambah" in kr:
        k["langkah"].append(kr["langkah_tambah"])
    k["harapan"]["koreksi"] = "lihat KOREKSI di konversi_laporan11.py"
    return k


if __name__ == "__main__":
    baris = [json.loads(l) for l in open(sys.argv[1]) if l.startswith("{")]
    print(json.dumps({"versi": 1, "sumber": "laporan11 (MASTER claude -p, BE c8c24092), dikonversi + dikoreksi",
                      "kasus": [koreksi(ubah(r)) for r in baris]}, ensure_ascii=False, indent=1))
