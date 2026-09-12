# TIKET — `/api/expenses/ledger` tak bisa menampilkan beban yang DIBATALKAN (12 Sep 2026)

**Status:** TERBUKA. **Tidak diperbaiki** — memperbaikinya mengubah arti endpoint,
dan itu putusan pemilik. Dokumen ini mengukur, tidak memilih.

**Lingkup verifikasi:** kode + kueri baca ke DB produksi. Nol perubahan.

## Cacatnya

Saat beban di-void, jurnal aslinya di-set **`journal_entries.status = 'VOID'`**.
CTE `expense_ledger` menyaring `AND je.status = 'POSTED'`, sehingga **setiap
jurnal beban yang dibatalkan terbuang di hulu**.

Terukur (`kaos-biru-konveksi`), dari 22 beban berjurnal:

| status beban | status jurnal | lolos ke ledger |
|---|---|---|
| `posted` | `POSTED` | **6 ya** |
| `void` | `VOID` | **16 TIDAK** |

## Akibatnya: satu cabang penyaring MUSTAHIL menyala

`expenses.py` ~784:
```python
elif status == "void":
    conditions.append("(el.is_reversed = true OR e.status = 'void')")
```

Cabang `e.status = 'void'` **tak akan pernah cocok**: tak ada baris ber-status
void yang selamat dari CTE sampai ke kueri luar. Klausanya **ditulis benar dan
mati secara struktur** — ia terbaca seperti fitur yang ada.

Kelas yang sama dengan penjaga-penjaga mati yang didokumentasikan hari ini
(`penjaga-menyaring-kosakata-mati`), tapi di **jalur baca**: bukan gagal menjaga,
melainkan gagal menampilkan.

## Yang dilihat pengguna

`ExpenseDesktop.tsx:227` memanggil `ledger?limit=1&status=void` dan memakai
`total` untuk pil **"Dibatalkan"**.

- Yang ditampilkan: **2** — dua jurnal `PRODUCTION_VARIANCE` yang terbalik.
- Yang sebenarnya dibatalkan pemilik: **16**.

**Pilnya BERANGKA**, jadi ia tak terlihat rusak. Angka yang salah lebih
meyakinkan daripada angka yang kosong — dan tak ada parser yang bisa
menangkapnya; hanya perbandingan dua sumber.

## Isi `ledger` yang sebenarnya (9 baris)

| | baris | punya dokumen beban |
|---|---|---|
| `is_reversed = false` | 7 | 6 (semua `posted`) |
| `is_reversed = true` | 2 | 0 |

⚠️ **Koreksi angka yang sempat beredar**: "MANUAL 65 / EXPENSE 22 /
BANK_TRANSACTION 1" **keliru** — cacahan itu melewatkan TIGA penyaring
(`account_type IN ('EXPENSE','OTHER_EXPENSE')`, `jl.debit > 0`,
`je.reversal_of_id IS NULL`). Ia benar untuk pertanyaan lain, lalu dipakai untuk
pertanyaan ini. Dari angka itu sempat diedarkan konsekuensi "66 baris lenyap";
angka sebenarnya **3**.

## Pertukaran sesungguhnya, kalau daftar Beban pindah ke `/api/expenses`

| | |
|---|---|
| **hilang** | **3** jurnal `PRODUCTION_VARIANCE` (tanpa dokumen beban) |
| **didapat** | **16** beban void yang hari ini tak terlihat, **+ draf** |

Rekomendasi (berubah sesudah pengukuran ini): **pindah**, atau tampilkan
keduanya. Rekomendasi sebelumnya — "jangan sentuh `ledger`" — bersandar pada
anggapan bahwa `ledger` lebih lengkap. **Ia tidak.**

## KONFIRMASI EMPIRIS (12 Sep, sesi FRONTEND)

Bagian di atas semula **diturunkan dari kode + sifat struktural**. Kini
**teramati**, dan pembedaan itu layak tercatat:

```
POST /api/expenses {status:"draft"}  -> 201  EXP-2609-0033  status=draft
ledger?status=all                    -> total 9   memuat draf? TIDAK
GET /api/expenses?limit=100          -> 23 baris  memuat draf? YA (draft 1 · void 16 · posted 6)
DELETE /api/expenses/{id}            -> 200, jurnal 415 -> 415, NOL jejak
```

**Dan lingkupnya lebih luas dari dugaan awal: KEDUA permukaan membaca `ledger`**,
bukan hanya desktop —
`ExpenseDesktop.tsx:192`, `Expenses/index.tsx:220` (ponsel), `useExpenseList.ts:389`.
Jadi kelima cabang logika-draf di FE mustahil menyala di dua-duanya.

### Akibat hidup sejak rilis FE 44

Sebelum rilis 44 form Beban tak mengirim `status`, jadi server **selalu
menerbitkan**: pengguna mendapat **jenis dokumen yang salah, tapi TERLIHAT**
(punya jurnal → muncul di `ledger`). Sesudah 44 ia mendapat **jenis yang benar,
tapi TIDAK TERLIHAT**.

Itu **bukan kemunduran**: jalur draf (BE) dan penyambungan layar (FE)
masing-masing benar sendiri-sendiri. Yang salah adalah daftar yang menampilkannya
membaca sumber yang secara struktur tak bisa melihatnya — cacat yang sudah ada
sejak `ledger` dipakai sebagai sumber daftar KERJA, dan baru tampak ketika ada
dokumen tak-berjurnal untuk ditampilkan.

## Tiga arah perbaikan, kalau pemilik memilih menyentuh BE

1. **Jangan apa-apakan.** `ledger` tetap "buku besar yang terbukukan"; cabang
   `e.status='void'` **dicabut** karena ia berbohong. Pil Dibatalkan pindah
   sumber ke `/api/expenses`.
2. **Loloskan jurnal VOID ke CTE** (`je.status IN ('POSTED','VOID')`) dan tandai
   di baris. Menambah kelengkapan, tapi **merusak jaminan** "setiap baris di
   sini terbukukan" yang dipakai untuk Law 16.
3. **Jangan set `je.status='VOID'` saat void**, andalkan jurnal pembalik saja.
   Paling bersih secara akuntansi, **paling berisiko** — menyentuh jalur void
   yang dipakai modul lain.

Arah 1 nol risiko akuntansi; arah 3 paling benar tapi paling luas. Itu putusan
pemilik, bukan putusan yang boleh diambil sambil lewat.
