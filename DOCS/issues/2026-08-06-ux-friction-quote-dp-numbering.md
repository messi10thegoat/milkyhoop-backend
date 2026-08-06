# P2: Friksi UX dari walkthrough E2E — form penawaran, DP, penomoran, label

**Tanggal:** 2026-08-06 **Severity:** P2 (pengguna mentok/bingung; angka tetap benar)
**Status:** OPEN **Sumber:** owner mengeklik sendiri di UI produksi

Dikumpulkan jadi satu tiket karena semuanya lapisan presentasi dan murah dikerjakan sekaligus.

---

## 1. Field Diskon tidak bisa ditap (dilaporkan owner)

`components/app/Quote/CreateQuote/index.tsx:772` — pill DISKON:
```tsx
onClick={() => toggleField('discount')}      // hanya MEMBENTANGKAN
onActionClick={() => setActiveSheet('discount')}  // ini yang membuka sheet
```
Sheet diskon hanya terbuka lewat `onActionClick`, yaitu tombol bundar **32px** bertanda `+` di sisi
kanan (`FieldPill.tsx:120-142`), atau tap **kedua** pada area yang baru terbentang
(`FieldPill.tsx:147-155`). Tap pertama pada label karena itu tampak tidak melakukan apa-apa.

**⚠️ BELUM PASTI kontrol mana yang tampil di layar owner.** Ada DUA kontrol diskon di file yang sama:
baris 332 (`onClick` langsung `setActiveSheet('discount')` — seharusnya bekerja) dan baris 772
(dua-tap). Tangkapan layar dibutuhkan sebelum menambal, supaya tidak memperbaiki yang salah.

**Saran:** target tap utama harus melakukan aksi utama. Buat `onClick` pill langsung membuka sheet,
atau tampilkan editor inline yang berarti saat terbentang.

## 2. Picker rekening tersembunyi di balik toggle

`CreateQuote/index.tsx:1077-1090` — field **"PILIH REKENING"** hanya dirender bila
`form.formData.showBankAccount` menyala.

Owner **melewatkannya sepenuhnya** pada percobaan pertama → penawaran tersimpan dengan ketiga field
bank kosong, dan langkah 1 harus diulang dengan penawaran baru.

**Kenapa penting:** alur DP mensyaratkan rekening tujuan (itu yang dicetak di PDF supaya pelanggan
bisa transfer). Menyembunyikan input wajib-secara-praktis di balik toggle membuat jalur utama gagal
diam-diam. Ini bukan kesalahan pengguna, ini rancangan.

**Saran:** kalau DP diisi, tampilkan picker rekening secara default (atau tandai sebagai perlu diisi).

## 3. Angka DP tidak di-prefill saat "Terima DP"

Penawaran menjanjikan DP 30% = 1.500.000. Saat membuka "Terima DP" dari halaman SO, pengguna harus
mengetik sendiri angkanya.

Jejaknya **ada** di layar SO (kartu "UANG MUKA DITERIMA" + "Dari Penawaran QUO-2608-0002"), jadi ini
lebih ringan dari dugaan awal — yang kurang hanya nilai anjuran di form.

Catatan arsitektur (bukan bug): `sales_orders` memang **nol kolom DP** — DP berjangkar di
`customer_deposits` yang ditautkan ke SO. Prefill harus membaca dari quote yang tertaut.

**Saran:** prefill jumlah DP dari `quotes.dp_amount` lewat `sales_orders.quote_id`.

## 4. Format nomor tidak konsisten antara UI dan jurnal

Kwitansi penerimaan pembayaran tampil di UI sebagai **`RCV-2026-0001`**, sedangkan jurnalnya
**`RCV-2608-0001`**. Format tahun berbeda: `2026` (tahun penuh) vs `2608` (yy+mm, dipakai semua
dokumen lain: `PB-2608`, `QUO-2608`, `SO-2608`, `INV-2608`, `DEP-2608`, `SJ-2608`).

Nol dampak angka, tetapi **menyulitkan penelusuran audit** — dua nomor untuk satu peristiwa.

**Saran:** samakan ke pola `yymm`.

## 5. Label "Margin 30%" ambigu di detail produk

Kartu MARGIN di halaman Barang & Jasa menampilkan margin **master data** (harga jual 50.000 vs harga
beli 35.000), bukan margin **realisasi**. Kebetulan hasil akhir walkthrough juga 1.500.000 / 30%,
sehingga mudah tertukar saat verifikasi.

**Saran:** beri label eksplisit ("Margin rencana" / "dari harga master").

---

## Yang GUGUR setelah diperiksa (jangan dikerjakan)

- **Prefiks jurnal bentrok** — TIDAK bentrok. `PJ-` (faktur pembelian), `JV-` (faktur penjualan),
  `BP-`/`VD-` (bayar/void), `DEP-`/`DA-` (DP/apply), `COGS-`, `RECOG-`, `RCV-`. Seri terpisah semua.
  `PJ` untuk pembelian memang membingungkan dibaca, tapi nol risiko tabrakan nomor.
