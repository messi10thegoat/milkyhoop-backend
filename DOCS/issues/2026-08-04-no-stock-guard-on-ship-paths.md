# Nol guard stok di kedua jalur pengiriman — stok bisa negatif, COGS 0, laba kotor palsu

**Tanggal:** 2026-08-04  **Severity:** HIGH (integritas akuntansi, bukan sekadar UX)
**Status:** OPEN — **butuh bukti runtime.** Kelas bukti saat ini `[CODE]` saja.
**JANGAN diuji di tengah walkthrough** (mengotori tenant + menguji hal lain).

## Klaim

Tidak ada guard "stok tidak cukup" di **kedua** jalur tulis pengiriman:

| Jalur tulis | Lokasi | Guard stok |
|---|---|---|
| `POST /api/sales-invoices/{id}/fulfill` | `routers/sales_invoices.py:4628` | **nol match** |
| `POST /api/sales-orders/{id}/ship` | `routers/sales_orders.py:1002` | **nol match** |

Grep: `insufficient|stok tidak cukup|not enough|available_quantity *<`. Satu-satunya guard sejenis
di seluruh `app/` ada di `routers/items.py:3656` (`"Insufficient stock. Available: …, Requested: …"`)
— itu jalur **stock transfer/adjustment**, bukan pengiriman.

## Koreksi atas laporan awal

Laporan pertama sesi ini menyebut guard tak ada di `deliveries.py`. **Grep itu tak bermakna:**
`deliveries.py` (493 baris) **read-only** — hanya 4 endpoint GET (`/summary`, `""`, `/{id}`,
`/{id}/pdf`), nol tulis, nol sentuh `inventory_ledger`/`warehouse_stock`. Docstring-nya sendiri:
*"Read-only endpoints for delivery management (invoice_fulfillments wrapper). Create still uses
POST /api/sales-invoices/{id}/fulfill."* Mencari guard tulis di file yang tak pernah menulis
akan selalu mengembalikan nol — itu persis anti-pola **Law 33** (gate yang mustahil menyala).
Klaim di atas adalah hasil grep ulang pada jalur tulis yang benar.

**Bonus temuan: pengiriman TETAP dualitas, bukan trialitas.** `deliveries.py` ter-mount di
`/api/deliveries` (`main.py:729`) tapi ia fasad baca di atas `invoice_fulfillments`, bukan jalur
tulis ketiga.

## Konsekuensi bila benar (perlu dibuktikan runtime)

Mengirim tanpa stok akan lolos diam-diam, dan:
1. **Stok jadi negatif** — keadaan fisik yang mustahil, dilaporkan sebagai fakta.
2. **COGS = 0** (WAC nol karena tak pernah ada inbound) → jurnal `Dr HPP 0 / Cr Persediaan 0`,
   atau tak terbentuk sama sekali.
3. **Laba kotor palsu 100% margin** — pendapatan diakui penuh, beban pokok nol.

Untuk produk akuntansi ini berat: bukan kosmetik, melainkan **laporan laba-rugi yang salah secara
material** tanpa satu pun sinyal ke pengguna. Masuk kelas silent-fallback (error menyamar jadi state
normal yang plausibel). Bandingkan Law 16 — angka harus journal-derived; di sini jurnalnya sendiri
yang lahir salah karena input tak divalidasi.

## Yang BELUM dibuktikan

`[CODE]` menunjukkan **ketiadaan string guard**. Itu tidak sama dengan ketiadaan guard:
- validasi bisa ada di service layer / DB trigger / CHECK constraint yang tak kena pola grep;
- pesan errornya bisa berbahasa lain atau dirakit dinamis.

Per **Law 33**, ketiadaan match dari satu grep belum boleh jadi verdict. **Butuh uji runtime.**

## Usul skenario harness #3 — `ship-without-stock`

Digabung dengan skenario #2 (`auto-fulfill`) yang sudah difile, di `scripts/e2e/dp_flow/`:

```
Given tenant fresh, produk track_inventory=true, stok 0, nol inbound
When  POST /api/sales-invoices/{id}/fulfill  (qty > 0)
Then  HARUS ditolak (4xx) DENGAN pesan yang bisa dibaca pengguna
      DAN warehouse_stock.quantity tidak pernah < 0
      DAN nol jurnal COGS bernilai 0 terbentuk
```
Ulangi untuk `POST /api/sales-orders/{id}/ship` — **kedua** jalur harus diuji terpisah; menguji satu
dan menyimpulkan yang lain adalah `[INFER]`.

**Uji-bicara (Law 33) untuk skenario ini:** jalankan lebih dulu pada tenant yang stoknya CUKUP dan
pastikan skenario LULUS (hijau) — supaya "merah" pada stok-nol benar-benar berasal dari kondisi yang
diuji, bukan dari skenario yang selalu merah.

## Terkait
- Dualitas jalur pengiriman (`/ship` vs `/fulfill`) — sudah difile terpisah.
- Ditemukan saat persiapan Bagian B walkthrough 2026-08-04; itulah sebabnya urutan walkthrough
  direvisi memakai **pembelian nyata** (bukan stock adjustment) untuk mengisi stok, supaya WAC
  35.000 terbentuk benar dan COGS 3.500.000 dapat diuji.
