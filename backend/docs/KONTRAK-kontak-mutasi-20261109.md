# KONTRAK — Mutasi piutang/hutang per kontak (9 Nov 2026)

Status: DIKODE 9 Nov 2026 (services/kontak_mutasi.py; cabang feat/kontak-mutasi). Semula rancangan untuk stub FE (K-A). Putusan MASTER 9 Nov: tampilan baca-saja dari jurnal yang sudah ada, pola Accurate
"rincian piutang/hutang per pelanggan/pemasok" (buku besar pembantu). BUKAN aturan uang baru. Kode BE menyusul (celah 3);
bentuk di bawah ini mengikat. Perubahan bentuk lewat MASTER.

## Rute

```
GET /api/customers/{id}/mutasi?dari=YYYY-MM-DD&sampai=YYYY-MM-DD&tanpa_batal=true&skip=0&limit=50
GET /api/vendors/{id}/mutasi?dari=YYYY-MM-DD&sampai=YYYY-MM-DD&tanpa_batal=true&skip=0&limit=50
```

| param | bawaan | arti |
|---|---|---|
| `dari` | null (sejak awal) | baris sebelum `dari` dijumlah ke `saldo_awal` |
| `sampai` | null (semua) | batas akhir (inklusif). Bawaan kosong supaya dokumen bertanggal ke depan tetap ikut dan paritas berlaku |
| `tanpa_batal` | `true` | `true` = jurnal yang dibatalkan DAN pembaliknya disaring dua sisi (sama dengan saldo). `false` = keduanya tampil berpasangan (`batal: true`), netonya nol |
| `skip`, `limit` | 0, 50 (maks 200) | halaman atas urutan naik |

## Sumber angka (paritas dijamin konstruksi)

- Akun: Piutang usaha (pelanggan, `account_type RECEIVABLE`) / Hutang usaha (vendor, `PAYABLE`). Jurnal POSTED saja, tanpa draf.
- Baris = komponen yang SAMA dengan penentu `compute_ar_outstanding` / `compute_ap_outstanding` per jurnal (faktur/tagihan,
  pembayaran per alokasi, nota kredit/debit, pemakaian uang muka, lepas pembayaran, nota tak tertaut, saldo awal). `journal_lines`
  tidak menyimpan pihak, jadi pihak diambil dari dokumen sumber, persis seperti penentu saldo. Jurnal umum manual ke akun
  piutang/hutang tanpa dokumen pihak TIDAK masuk saldo kontak, jadi juga tidak tampil di sini.
- Saldo berjalan: pelanggan `saldo = saldo_awal + Σ(debit − kredit)`; vendor `saldo = saldo_awal + Σ(kredit − debit)`.
- **Paritas (dites, scripts/ops/uji-salinan-mutasi.sh):** tanpa `dari` dan `sampai` → `saldo_akhir` == `saldo` di detail kontak
  (== Σ `outstanding` penentu yang sama, kontak_daftar). Baik `tanpa_batal=true` maupun `false`.
- Uang muka pelanggan / saldo kredit = akun lain → TIDAK di mutasi (kartu terpisah, celah 4).
- Urutan: `tanggal` naik, lalu nomor jurnal, lalu urutan komponen. `saldo` tiap baris dihitung atas SELURUH urutan (window di
  server), jadi halaman 2 meneruskan saldo halaman 1.

## Respons 200

```json
{
  "success": true,
  "data": {
    "pihak": {"id": "7f1c…", "nama": "UJI Toko Sinar", "jenis": "customer"},
    "akun_text": "Piutang usaha",
    "dari": "2026-11-01",
    "sampai": "2026-11-09",
    "tanpa_batal": true,
    "saldo_awal": 250000,
    "total_debit": 1500000,
    "total_kredit": 500000,
    "saldo_akhir": 1250000,
    "total": 3,
    "skip": 0,
    "limit": 50,
    "baris": [
      {
        "id": "c5986e45-…:1:3b06…",
        "tanggal": "2026-11-03",
        "jurnal": {"id": "c5986e45-…", "nomor": "INV-2611-0004"},
        "dokumen": {"tipe": "sales_invoice", "id": "3b06…", "nomor": "INV-2611-0004"},
        "jenis_text": "Faktur penjualan",
        "keterangan": "INV-2611-0004",
        "debit": 1500000,
        "kredit": 0,
        "saldo": 1750000,
        "batal": false
      },
      {
        "id": "5692d7a0-…:2:9a1e…",
        "tanggal": "2026-11-05",
        "jurnal": {"id": "5692d7a0-…", "nomor": "RCV-2611-0002"},
        "dokumen": {"tipe": "receive_payment", "id": "9a1e…", "nomor": "RCV-2611-0002"},
        "jenis_text": "Pembayaran masuk",
        "keterangan": "untuk INV-2611-0004",
        "debit": 0,
        "kredit": 500000,
        "saldo": 1250000,
        "batal": false
      }
    ],
    "omitted": []
  }
}
```

Medan:
- Uang = JSON number (Rupiah bulat → int, bersen → float; `schemas/uang_json`), sama dengan medan uang lain.
- `dokumen.tipe` ∈ pelanggan: `sales_invoice`, `receive_payment`, `credit_note`, `deposit_application`;
  vendor: `bill`, `bill_payment`, `vendor_credit`, `deposit_application`, `opening_balance`. Rute FE = tipe + id.
  `deposit_application`: id/nomor = uang muka yang dipakai. Lepas pembayaran = tipe `sales_invoice` (jenis_text "Lepas pembayaran").
  `opening_balance` vendor: id = vendor, nomor null. Baris batal (tanpa_batal=false): dokumen.nomor null, pakai `jurnal.nomor`;
  pembalik: jenis_text "Pembatalan …", keterangan "membatalkan <nomor jurnal asal>".
- `jenis_text` = teks server siap tampil (FE tak memetakan tipe → teks).
- `batal` hanya bisa `true` bila `tanpa_batal=false`.
- `id` baris = `<jurnal>:<komponen>:<dokumen>` (unik; satu jurnal pembayaran bisa berbaris per faktur yang dilunasi).
- `omitted`: pola B4. Tanpa izin BACA modul dokumen → `dokumen.nomor` & `keterangan` = null untuk baris modul itu, angka tetap
  (angka = saldo kontak yang sudah boleh dibaca), `omitted` = [modul].

## Galat

| kode | kapan | detail |
|---|---|---|
| 404 | kontak tak ada di tenant ini | "Pelanggan tidak ditemukan." / "Vendor tidak ditemukan." |
| 400 | `dari` > `sampai` | "Tanggal awal tidak boleh setelah tanggal akhir." |
| 422 | format tanggal salah | bawaan FastAPI |

## Tidak berubah

`GET /{id}/transactions` (lama, tabel pembungkus) dibiarkan untuk FE lama; `GET /{id}/journal-entries` tetap.
