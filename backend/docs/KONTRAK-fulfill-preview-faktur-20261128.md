# KONTRAK BE — Pratinjau "Kirim barang" Faktur Penjualan + batch/serial di SO-fulfill (BUG-430, 28 Nov 2026)

Status: KONTRAK (kode menyusul, tanpa migrasi, tanpa flag baru). Pola = `POST /api/sales-orders/{id}/fulfill/preview`: inti SAMA dengan tulis, dijalankan di transaksi yang SELALU di-ROLLBACK -> nol tulis. Izin = sama dengan `POST /api/sales-invoices/{id}/fulfill` (modul faktur penjualan, aksi tulis/kirim) — tidak ada pemetaan izin baru berbeda dari rute kembarannya.

## A. `POST /api/sales-invoices/{invoice_id}/fulfill/preview`

Badan = badan `POST /fulfill` (persis, tanpa kolom baru):
```json
{
  "warehouse_id": "uuid|null",
  "fulfillment_date": "2026-11-28",
  "notes": "string|null",
  "recognize_revenue": true,
  "items": [
    {"invoice_item_id": "uuid", "quantity": "6", "batch_id": "uuid|null", "serials": ["SN-001"]}
  ]
}
```
`batch_id` kosong = FEFO otomatis (hasil pecahan terlihat di `lines[].batches`). `serials` hanya untuk barang ber-serial (flag serial_v1).

### Respons — SELALU HTTP 200 (juga bila diblok; tulis = POST /fulfill dengan badan yang sama)
```json
{
  "success": true,
  "data": {
    "can_fulfill": true,
    "blocks": [],
    "warnings": [{"code": "MASIH_SISA", "message": "Sisa Kaos Polos 4 Pcs belum dikirim."}],
    "invoice": {"id": "uuid", "number": "INV-2611-0007", "customer_name": "PT Contoh",
                "fulfillment_status_before": "unfulfilled", "fulfillment_status_after": "partial"},
    "warehouse": {"id": "uuid", "name": "Gudang Utama"},
    "fulfillment_date": "2026-11-28",
    "lines": [
      {
        "invoice_item_id": "uuid", "description": "Kaos Polos", "unit": "Dus",
        "quantity": "6", "quantity_text": "6 Dus",
        "ordered_text": "10 Dus", "remaining_before_text": "10 Dus", "remaining_after_text": "4 Dus",
        "stock_before_text": "20 Dus", "stock_after_text": "14 Dus",
        "batches": [
          {"batch_id": "uuid", "batch_no": "B-2611-01", "expiry_text": "31 Des 2026",
           "quantity": "4", "quantity_text": "4 Dus", "available_before_text": "4 Dus"},
          {"batch_id": "uuid", "batch_no": "B-2611-02", "expiry_text": null,
           "quantity": "2", "quantity_text": "2 Dus", "available_before_text": "10 Dus"}
        ],
        "serials": [], "cogs_text": "Rp 840.000"
      }
    ],
    "totals": {"cogs": "840000.00", "cogs_text": "Rp 840.000", "revenue": "1000000.00", "revenue_text": "Rp 1.000.000"},
    "journal_text": "HPP Rp 840.000 dijurnal Dr HPP / Cr Persediaan; pendapatan Rp 1.000.000 diakui."
  }
}
```
Semua `*_text` = teks SERVER siap tampil (FE tidak memformat/menghitung). Angka `quantity`/`cogs`/`revenue` string desimal apa adanya dari inti (angka journal-derived; FE hanya menampilkan). `lines[].batches` terisi hanya untuk barang ber-batch; `serials` hanya barang ber-serial. Bila diblok: `can_fulfill=false`, `lines` tetap memuat baris yang bisa diukur, `totals`/`journal_text` = null.

### Blok berkode (`blocks[]` = `{"code","message","invoice_item_id?"}`; pesan Indonesia ramah, tanpa istilah teknis)
| code | arti |
|---|---|
| `ITEMS_KOSONG` | tak ada baris dipilih |
| `GUDANG_WAJIB` | ada baris ber-stok tapi gudang kosong |
| `BARIS_BUKAN_MILIK_FAKTUR` | invoice_item_id bukan baris faktur ini |
| `SISA_KURANG` | jumlah > sisa belum terkirim ("Sisa X hanya N, diminta M.") |
| `BATCH_STOK_KURANG` | stok batch pilihan kurang (kosakata dipegang BACKEND 3, BUG-431: kode & pesan SAMA di SO-fulfill, faktur, surat jalan) |
| `STOK_KURANG` | stok total gudang kurang (tanpa batch) |
| `SERIAL_WAJIB` / `SERIAL_JUMLAH_TAK_SAMA` / `SERIAL_TAK_TERSEDIA` | barang ber-serial: nomor seri belum diisi / jumlah ≠ qty dasar / nomor tak ada di gudang |
| `FAKTUR_TAK_BISA_DIKIRIM` | status faktur (draf/batal/…) tak boleh kirim |
| `PERIODE_TERTUTUP` | tanggal di periode terkunci (Law 5; HTTP preview tetap 200, tulis = 403) |
| `KIRIM_DITOLAK` | penolakan inti lain; `message` = teks server apa adanya |
Galat jalur (id bukan UUID/faktur tak ada tenant ini) = 404 `{"detail":"Faktur tidak ditemukan."}` sebelum DB; 422 untuk badan salah bentuk.

Catatan keras: preview TIDAK menyimpan nomor Surat Jalan/jurnal (rollback); nomor pada hasil tulis bisa berbeda.

## B. `POST /api/sales-orders/{id}/fulfill` dan `/fulfill/preview` — `batch_id?`/`serials?` per baris

`SOFulfillRequest.items[]` kini: `{"sales_order_item_id": "uuid", "quantity": "6", "batch_id": "uuid|null", "serials": ["SN-1"]}` — diteruskan apa adanya ke inti faktur (sama dengan A). **`extra=forbid`** pada item DAN badan: kolom tak dikenal = 422 (dulu diabaikan diam-diam). Bentuk respons preview/tulis SO TIDAK berubah, kecuali `lines[].batches`/`serials` kini terisi sesuai pilihan. Klien lama yang hanya mengirim kolom lama tetap valid.
