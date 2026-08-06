# P1: FE tidak menyegarkan setelah mutasi berhasil → risiko transaksi ganda

**Tanggal:** 2026-08-06 **Severity:** P1 (bukan kosmetik — risiko uang dobel)
**Status:** OPEN **Kelas bukti:** `[SQL]` + `[HTTP]` + observasi owner, terkonfirmasi 3×

## Gejala

Aksi berhasil di backend, tetapi layar tidak berubah. Pengguna menyimpulkan aksinya **gagal**.

Tiga kejadian dalam satu sesi walkthrough:

| # | Aksi | Kata layar | Kata data |
|---|---|---|---|
| 1 | Simpan penawaran + "Kirim Penawaran" | badge **"Draf"** | `status='sent'`, `sent_at` terisi |
| 2 | Tap **"Terima DP"** | tak ada perubahan | `customer_deposits` +1, jurnal 7→8, `DEP-2608-0001` posted |
| 3 | Daftar setelah mutasi | entri lama | baris baru ada di DB |

Kasus #2 dikonfirmasi murni cache: setelah **refresh manual**, kartu "UANG MUKA DITERIMA —
DEP-2608-0001 Rp1.500.000 posted" muncul benar di halaman SO.

## Kenapa ini P1, bukan kosmetik

Pengguna yang yakin aksinya gagal akan **mengulanginya**. Di modul uang itu berarti:
- Terima DP dua kali → dua deposit → uang muka dobel
- Terima/Kirim Pembayaran dua kali → kas salah, settlement dobel

Yang menyelamatkan pada kasus DP kemarin **hanya** `idempotency_key` di `customer_deposits`
(terlihat di row: `idempotency_key = 4e58f927-…`). Itu tak ada di semua jalur tulis — jadi
perlindungannya tidak merata dan tidak boleh diandalkan sebagai desain.

Ini kelas **silent-fallback terbalik**: bukan error yang menyamar jadi normal, melainkan
**sukses yang menyamar jadi gagal**. Sama berbahayanya, karena mendorong aksi destruktif.

## Akar (hipotesis kuat, perlu ditunjuk tepat)

Cache TanStack Query tidak di-invalidate setelah mutasi. Repo sudah punya pola `useXxxInvalidators`
(lihat `milkyhoop-api` + memory `fetch-arch-migration`), tetapi tidak dipakai konsisten di jalur:
- Quote create/send (status badge)
- Customer deposit create (halaman SO + daftar Uang Muka)
- daftar setelah create/post pada umumnya

## Perbaikan yang disarankan

**Jangan tambal per-halaman.** Audit seluruh mutasi, pastikan tiap `mutateAsync` sukses memanggil
invalidator yang sesuai. Pertahankan juga `dispatchDataChanged` yang sudah ada (defensive
double-emit, sesuai Frontend Law A).

Verifikasi wajib **dua sisi** (Law 33): buktikan test-nya bisa MERAH — jalankan pada versi tanpa
invalidasi dan pastikan gagal, bukan hanya lulus pada versi yang sudah diperbaiki.

## Mitigasi sementara

Pastikan **semua** endpoint tulis bernilai uang punya `idempotency_key` (audit: mana yang belum).
Itu perlindungan yang benar terlepas dari perbaikan FE.
