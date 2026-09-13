# TIKET — pembalikan jurnal GENERIK memutus ikatan tanpa menyentuh dokumennya

**Status:** TERBUKA, **melingkupi saja**. Nol perbaikan, nol sentuhan data.
**Menyentuh Law 31 Gate 5 (reversal cascade) dan Law 29/31 Gate 4.**

## Bentuknya

`POST` pembalikan jurnal di `journals.py` (INSERT di baris ~698) membuat jurnal
`source_type=MANUAL` ber-`reversal_of_id`. Ia memeriksa **empat** hal:
jurnal ada, bukan DRAFT, belum pernah dibalik, periode terbuka.

**Yang TIDAK diperiksanya:**

1. **`validate_no_derived_layer_accounts()` TIDAK dipanggil.** Validator itu ada
   dan benar — tapi dipasang di jalur **PEMBUATAN** (dipanggil baris 483), bukan
   di jalur **PEMBALIKAN**. Pagarnya di satu pintu, bukan dua.
   Ia lahir 26 Mei (`e39eaeab`), jadi ini bukan "pagar belum ada".
2. **`source_type` sasaran tidak dibatasi.** Jurnal berpunya-dokumen (BILL,
   INVOICE) dan berpunya-layer (PRODUCTION_*) bisa dibalik lewat pintu generik
   ini, sehingga **dokumen sumber dan layer turunannya tak ikut dibalik**.

## Terukur (`kaos-biru-konveksi`, semuanya 3 Sep 2026)

**65 jurnal MANUAL, SEMUANYA pembalikan** (65/65 ber-`reversal_of_id`; nol
jurnal manual biasa). Yang mereka balik:

| source_type yang dibalik | jml | nilai |
|---|---|---|
| PRODUCTION_OVERHEAD | 24 | 5.620.000 |
| PRODUCTION_LABOR | 23 | 5.700.000 |
| **BILL** | **9** | **6.680.000** |
| PRODUCTION_OUTPUT | 5 | 5.459.968 |
| PRODUCTION_VARIANCE | 4 | 800.000 |

~Rp 24 juta lintas **lima** source_type, nol cascade ke layer turunan.

**Akibat yang sudah terlihat:** sembilan tagihan ber-jurnal-terbalik tetap
berbunyi `posted`/`paid` dengan `accounting_status=POSTED` — termasuk
`BILL-2609-0002` yang berbunyi **`paid`**. Jurnalnya terbalik, dokumennya tidak.

⚠️ **`void_bill` sendiri BENAR** — ia menulis `status=void`, `status_v2`,
`operational_status=VOID`, `accounting_status=REVERSED`. Jadi ini **bukan**
cacat jalur void. Cacatnya: **ada jalan lain memutus ikatan yang tak melewatinya.**

**Dugaan yang belum diukur:** pembalikan `PRODUCTION_OUTPUT` kemungkinan
meninggalkan `inventory_ledger` tak terbalik dengan cara yang sama — dan itu
mungkin asal 5 baris `PRODUCTION_OUTPUT` tanpa `journal_id` yang ditemukan saat
melingkupi drift WAC. **BELUM diukur; jangan dikutip sebagai temuan.**

## `check_13` MENGHADAP ARAH YANG SALAH

`check_13_status_desync` mencari *"bills with journal but `accounting_status !=
POSTED`"*. Kelas di atas adalah **kebalikannya**: `accounting_status=POSTED`
sementara jurnalnya sudah dibalik.

**Jadi memperbaiki SQL `check_13` TIDAK menutup kelas ini.** Siapa pun yang
membetulkan sintaksnya lalu mengira desync sudah terjaga akan keliru: penjaga
itu akan hijau dan tetap buta terhadap arah ini. Kelas ini tak terjaga **dua**
kali — sekali karena check_13 tak pernah berjalan (rusak sejak 2026-04-20),
sekali karena arahnya terbalik.

## Yang TIDAK dikerjakan

- **Sembilan dokumen itu TIDAK disentuh.** Merapikannya = menyunting pembukuan
  (Law 2/3) = putusan pemilik, dengan rencananya sendiri.
- Endpoint pembalikan TIDAK diubah. Ia menyentuh Law 31 dan menuntut 7/7 gate
  serta pembacaan companion skill lebih dulu.
