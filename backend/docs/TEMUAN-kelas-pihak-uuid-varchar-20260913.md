# TEMUAN — kelas pihak uuid-vs-varchar: sapuan lengkap (13 Sep 2026)

**Status:** TERUKUR, nol perbaikan, **nol migrasi tipe** (keputusan data = pemilik).

## Skema (information_schema, BASE TABLE)

33 kolom pihak. **Hanya 2 VARCHAR:** `credit_notes.customer_id`, `customer_deposits.customer_id`.
31 lainnya **uuid** (customer_id 14 tabel, vendor_id 16, supplier_id 1). Induk `customers`/`vendors`/`suppliers`.id
= uuid. **Sisi AP bebas dari kelas ini.**

## Data (kontrol sampah tertangkap 1→2)

| tabel | baris | kosong | bukan UUID | catatan |
|---|---|---|---|---|
| credit_notes | 7 | 0 | **1** | **CN-2608-0001** (grapgrap, posted, 2026-08-09): `customer_id = "Toko Melati"` — **NAMA, bukan id**. Pelanggan bernama itu: `15c07294-…` |
| customer_deposits | 78 | 43 (semua void) | 0 | bersih — kebetulan, pembuatnya tak memvalidasi |

## Situs (sapuan v2: jendela baris + 627 fungsi DB; 3 kontrol positif tertangkap)

Sapuan v1 (pasangan triple-quote) **TAK SAH** — melewatkan 2 situs yang sudah diketahui.

| situs | bentuk | hidup | akibat | bukti |
|---|---|---|---|---|
| `credit_notes.py:1507` terapkan CN | `UUID != str` Python | ya | tolak palsu SELALU → **fitur mati** | eksekusi |
| `receive_payments.py:1820` DP otomatis lebih bayar | tulis UUID → varchar | ya | **500** (+3 lapis lain) | eksekusi |
| `customers.py:1899/1908` (+count 2037/2045) `GET /api/customers/{id}/journal-entries` | **satu `$n`** untuk `si`/`rp` (uuid) DAN `cd`/`cn` (varchar), tanpa cast | **ya — FE `CustomerDetailPageDesktop.tsx:193`, `TransaksiTab.tsx:71`** | **500 untuk SETIAP pelanggan** — `operator does not exist: character varying = uuid` → **tab transaksi/jurnal detail pelanggan MATI** | eksekusi (pelanggan ber-DP & tanpa DP) |
| `credit_notes.py:640` buat CN | `str(body.customer_id)`, **tanpa validasi pelanggan** | ya | data kotor bisa masuk (CN-2608-0001) | baca; asal baris = DUGAAN kuat (satu-satunya INSERT) |
| `customer_deposits.py:862` buat DP | `body.customer_id` mentah, tanpa validasi | ya | sama; 0 kejadian | baca |
| `customers.py:1724` merge → customer_deposits | `$1::text`, `ANY($3::text[])` | ya | benar | baca |
| `customers.py:1730` merge → credit_notes | tanpa cast | ya | benar hari ini (body JSON = str; asyncpg terima str utk uuid & varchar); **rapuh** bila pemanggil kirim UUID | baca |
| `customers.py:1146` | `customer_id::text = str()` | ya | benar | baca |
| `customer_deposits.py:213` saldo DP pelanggan | `cd.customer_id = $2` | ya | bergantung pemanggil — DUGAAN aman bila str | baca |
| `receive_payments.py:1202` bayar dari DP | varchar = `body.customer_id` (str) | ya | benar (FIX_RCV_DEPOSIT_CUSTOMERID) | baca |
| `sales_invoices.py:4334` | `= str(invoice.customer_id)` | ya | benar (BATCH1 A1) | baca |
| `receive_payments.py:1253`, `:1482` | `str(uuid)` vs str | ya | benar (1253 peka huruf besar → gagal-tertutup) | baca |
| `tax_invoices.py:140` | uuid vs uuid | ya | benar, bukan kelas ini | baca |

Fungsi DB: 0 join/banding tanpa cast ke dua kolom varchar.

## Kolom hantu (kelas lain, disisipkan di sapuan ini)

`vendor_deposits.py:718` terapkan DP vendor ke tagihan: pernyataan UPDATE **persis dari kode** dijalankan di ROLLBACK
atas tagihan nyata → `ERROR: column "paid_amount" does not exist`. **Terbukti tingkat pernyataan**: bila handler
mencapainya, selalu gagal. Handler ujung-ke-ujung **tidak dieksekusi**: `vendor_deposits` 0 baris,
`vendor_deposit_applications` 0 (tak pernah dipakai).

## Kesimpulan

- Kelas **terbatas**: 2 kolom, 17 situs. **3 hidup-dan-rusak terbukti** (terapkan CN, DP otomatis, jurnal pelanggan)
  + **2 pembuat tanpa validasi** (sumber data kotor).
- Tambalan satu-per-satu yang sudah ada (`FIX_RCV_DEPOSIT_CUSTOMERID`, `BATCH1 A1`, merge `::text`) menunjukkan kelas ini
  menggigit berulang.
- **Akar yang menutup semuanya sekaligus:** migrasi dua kolom ke uuid. Syarat data: 1 baris "Toko Melati" + validasi di
  dua pembuat. **Putusan pemilik.**
- **Penghalang unit B nota kredit:** nilai = nama pelanggan; pembuat `POST /api/credit-notes` hidup tanpa validasi →
  perbaikan di **pembuat** (validasi + normalisasi), bukan hanya di penerap.
- Skrip: `scripts/sapu_pihak_skema.sql`, `scripts/sapu_pihak_kode.py` (v2), `scripts/ukur_pihak_eksekusi.py`.
- Di luar lingkup, dicatat saja: repo FE memuat berkas `src/receive_payments.py` (berkas Python di `src` frontend).
