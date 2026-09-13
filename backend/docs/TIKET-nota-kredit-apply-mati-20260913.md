# TIKET — terapkan nota kredit ke faktur: fitur MATI + atribusi piutang (13 Sep 2026)

**Status:** TERBUKA. **Putusan desain A/B menunggu pemilik** (pertanyaan produk: bolehkah satu
nota kredit dipecah ke beberapa faktur?). Nol kode.

## 1. Fitur mati — TERUKUR lewat eksekusi

`credit_notes.py` ~1504: `invoice["customer_id"] != cn["customer_id"]`.
`sales_invoices.customer_id` = **uuid**, `credit_notes.customer_id` = **varchar** → asyncpg
memberi `UUID` vs `str` → **selalu tidak sama** → **selalu 400 "belongs to different customer"**
bila nota kredit punya pelanggan. Dengan pelanggan yang teksnya identik, tetap 400 — dengan dan
tanpa pagar V244. `credit_note_applications` = **0 baris**: tak pernah berhasil sekali pun.

## 2. Premis yang SALAH dan dicabut

- **"Perbaikan tipe data"** bukan bentuk masalahnya (lihat 3).
- **"Samakan dengan pola DP"**: `apply_customer_deposit` **TIDAK memeriksa pelanggan sama
  sekali**. Menyamakannya = mencabut pemeriksaan pelanggan.
- **"AR efektif turun sebesar yang diterapkan"**: SALAH. Apply **tidak membuat jurnal**; piutang
  **sudah dikredit saat nota kredit DIPOSTING** (Dr REVENUE / Cr RECEIVABLE). Gerbang yang benar:
  saat apply **AR efektif TIDAK berubah**, yang berubah hanya **atribusinya** ke faktur. Gerbang
  yang menuntut AR turun akan memaksa jurnal kedua = hitung-ganda yang dilegalkan.

## 3. Atribusi — kenapa membetulkan tipe saja MERUSAK

`compute_ar_outstanding` menghitung kredit CN **hanya lewat `credit_notes.original_invoice_id`**,
bukan lewat `credit_note_applications`. Membetulkan perbandingan saja → apply menaikkan cache
`amount_paid` faktur sementara `compute_ar_outstanding` tetap outstanding penuh → **cache ≠ ledger
sejak lahir** (ARAP Rule 12).

## 4. Selisih AR HARI INI — TERUKUR

GL RECEIVABLE efektif vs Σ `compute_ar_outstanding`:

| tenant | GL | Σ per faktur | selisih | = |
|---|---|---|---|---|
| kaos-biru-konveksi | 2.260.000 | 2.285.000 | 25.000 | CN-2609-0006 (tanpa faktur asal) |
| grapgrap-manado | −180.000 | 20.000 | 200.000 | CN-2608-0001 (tanpa faktur asal) |

**Laporan piutang per faktur hari ini LEBIH BESAR dari buku besar** sebesar kredit nota kredit
yang tak terkait faktur.

`verify_ar_reconciliation_all()` **PASS** di atas selisih itu (total_gl = canonical). Definisi
GL-nya berbeda dari kueri Rule 8 skill arap. **Belum dibaca — belum diklaim buta.** Tiket kerja:
baca definisinya, buktikan buta/tidak lewat eksekusi (seperti R9).

## 5. Pilihan desain (pemilik)

- **A.** Apply mengaitkan kredit CN yang sudah diposting tanpa jurnal baru; `compute_ar_outstanding`
  diperluas menghitung `credit_note_applications` + dedup dgn `original_invoice_id`. Menyentuh
  fungsi Rule 5 (dashboard, aging, bot) — gerbang Rule 8 + semua pembaca.
- **B.** Apply mengisi `credit_notes.original_invoice_id` bila kosong (satu faktur per CN); fungsi
  tak berubah; menyunting kolom kaitan dokumen posted (bukan nominal).

## 6. DUGAAN terkait — DP lintas pelanggan

`apply_customer_deposit` tak memeriksa pelanggan → **DUGAAN**: DP pelanggan A bisa diterapkan ke
faktur pelanggan B (menulis ke piutang pelanggan yang salah). **Naikkan lewat eksekusi di
ROLLBACK.** Bila terbukti, prioritasnya di atas nota kredit.
