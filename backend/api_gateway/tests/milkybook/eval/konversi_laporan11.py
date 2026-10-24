"""Konversi set 60 kasus MASTER (laporan11, keluaran agen `claude -p`, BE c8c24092) ke format eval Milkybook.

Sumber BUKAN kebenaran: diverifikasi CONVERSATIONAL 24 Okt terhadap kode. Penyimpangan yang dikoreksi di sini:
- laporan11 menganggap buat pelanggan/barang DI LUAR pilot; rancangan §11.1 memasukkannya (tanpa saldo/stok awal).
  Jadi "create_item"/"POST /api/items" bukan larangan; yang dilarang tetap opening_stock*/ar_opening_balance.
- kirim/terima/tolak/duplikat/jadikan faktur di Milkybook = NAV (tak ada pratinjau BE), bukan tulis langsung.
- PPN tenant non-PKP ditolak 422 (services/pkp_guard.py:17, juga di /calculate quotes.py:873), bukan 400.
- Rute tanpa tool di paket (DELETE penawaran) = tak boleh ada tulis; jawaban jujur "tidak bisa dari sini".

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
}
NAV = {
    ("POST", r"/api/quotes/[^/]+/send"): "buka_kirim_penawaran",
    ("POST", r"/api/quotes/[^/]+/accept"): "buka_terima_penawaran",
    ("POST", r"/api/quotes/[^/]+/decline"): "buka_tolak_penawaran",
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


if __name__ == "__main__":
    baris = [json.loads(l) for l in open(sys.argv[1]) if l.startswith("{")]
    print(json.dumps({"versi": 1, "sumber": "laporan11 (MASTER claude -p, BE c8c24092), dikonversi + dikoreksi",
                      "kasus": [ubah(r) for r in baris]}, ensure_ascii=False, indent=1))
