# T144 FASE 2 — GATE: `create_item` menerima BANYAK barang

Tanggal ukur: 2026-08-28. Tenant: `kaos-biru-konveksi` SAJA.
master awal = `1b573133`. Lapisan ukur = sidecar `:8002` (image
`milkyhoop-dev-api_gateway`, bind-mount `/root/mh-t144-f2/backend/api_gateway`).

## Kenapa sidecar, bukan milkyhoop.com
Pra-merge, mengukur patch DI milkyhoop.com MUSTAHIL: main tree
`/root/milkyhoop-dev` terkunci di master dan ia adalah target deploy.
KONTROL yang membuat sidecar sah: dengan kode BELUM dipatch, M1 di `:8002`
mengembalikan respons BYTE-IDENTIK dengan milkyhoop.com (teks, payload,
`review_card`). Semua gate HIJAU-TETAP-HIJAU (G1–G5) juga dijalankan di
KEDUA lapisan dan dibandingkan.

## Kontrol negatif prosa "Array of ..." — hipotesis TIDAK terbukti
| langkah | description FieldSpec | `items` terisi? | tipe | isi |
|---|---|---|---|---|
| 1 | TANPA frasa "Array of ..." | YA, 2/2 | STRING berisi JSON | 5 baris, nama+harga BENAR |
| 2 | DENGAN "Array of items. ..." | YA, 2/2 | STRING berisi JSON | 5 baris, nama+harga BENAR |

Langkah 1 SUDAH hijau. Frasa itu NOL PENGARUH — pada terisinya field maupun
pada tipe keluarannya. Kesimpulan: selama ini kita menyalin JIMAT.
Deskripsi yang di-commit = versi TANPA frasa itu.
Bukti: `[EXTRACT_S2] intent=create_item extracted=['name','items','item_type','base_unit']`
+ `jsonb_typeof(pending_payload->'items') = string` pada KEDUA langkah.

## Verifikasi D6 — subfield `nama_produk` tidak bertabrakan
- 20 kemunculan `nama_produk` di backend (di luar `*_pb2`): seluruhnya
  (a) nama KOLOM `products`, (b) subfield baris item pada aksi LAIN, atau
  (c) pemetaan TAMPILAN entitas yang sudah ada (`update_item`/`delete_item`,
  orchestrator `_compact_current_data` + `_FIELD_RENAMES`).
- NOL penulis dan NOL pembaca `nama_produk` sebagai kunci TOP-LEVEL payload.
- `ENRICHERS` tidak memuat kunci `"CREATE_ITEM"` -> `_enrich_payload`
  melewati create_item -> nol penambal yang bisa menyentuhnya.
- KOREKSI atas usul D6: body `POST /api/items` memakai `name`, BUKAN
  `nama_produk` (`schemas/items.py` `CreateItemRequest.name`). `nama_produk`
  adalah nama KOLOM DB. Pemetaan subfield->body dilakukan EKSPLISIT.

## PAGAR `_resolve_item` — log diferensial
Probe permanen ditambahkan di `entity_resolver._resolve_item`.
| stimulus | `[RESOLVE_ITEM]` |
|---|---|
| M1 (create_item, 5 baris) | **0** |
| create_bill 2 baris (KONTROL POSITIF) | **1** (`fragment='Kain Katun'`) |

Kontrol positif membuktikan probe BISA menyala -> "nol" adalah PENGUKURAN.

## GATE M1–M5

### M1 — lima nama beda HANYA di dalam kurung
MERAH (milkyhoop.com, master `1b573133`), teks apa adanya:
```
### Buat Barang/Jasa
| Field | Value |
| Nama | Kaos Uji T144 (Size S) |
| Tipe | persediaan | Satuan | pcs |
| Harga Jual | Rp 100.000 | Harga Beli | Rp 60.000 |
```
payload `items` TIDAK ADA; 1 kartu, 1 barang, nol sebutan angka lima.

HIJAU:
```
### Buat 5 Barang/Jasa
| # | Nama | Harga Jual | Harga Beli | Satuan | Tipe |
| 1 | Kaos Uji T144 (Size S)   | Rp 100.000 | Rp 60.000 | pcs | persediaan |
| 2 | Kaos Uji T144 (Size M)   | Rp 110.000 | Rp 65.000 | pcs | persediaan |
| 3 | Kaos Uji T144 (Size L)   | Rp 120.000 | Rp 70.000 | pcs | persediaan |
| 4 | Kaos Uji T144 (Size XL)  | Rp 130.000 | Rp 75.000 | pcs | persediaan |
| 5 | Kaos Uji T144 (Size XXL) | Rp 140.000 | Rp 80.000 | pcs | persediaan |
```
EKSEKUSI (confirm) -> "5 dari 5 barang berhasil didaftarkan: ... (baris 1..5)."
[SQL] 5 baris `products` lahir, nama BERBEDA, `sales_price` 100000/110000/
120000/130000/140000, `purchase_price` 60000/65000/70000/75000/80000,
`item_code` BRG-0002..BRG-0006 (SKU auto-generate = bukti jalur
`POST /api/items`, bukan `bulk-import`).
`journal_entries` 11 -> 11. `inventory_ledger` 3 -> 3. NOL jurnal.

### M2 — tiga nama sangat berbeda
HIJAU: 3 baris utuh (Kaos Uji T144 Polos 90.000/50.000, Jaket Uji T144
Hoodie 250.000/160.000, Topi Uji T144 Trucker 75.000/40.000).

### M3 — satu baris TANPA harga beli
HIJAU: KEDUA baris tampil; baris 2 ditandai `⚠`; kartu memuat
"Baris 2 (Kaos Uji T144 Beta) belum lengkap: harga beli. Tetap bisa
didaftarkan." Eksekusi -> "2 dari 2 barang berhasil didaftarkan".
Bukan tolak semua, bukan senyap.

### M4 — empat belas barang
HIJAU: `message_type=TEXT`, `pending_action_id=null`, teks:
"Pesan ini memuat **14** barang sekaligus, sementara saya hanya sanggup
memproses 10 barang dalam satu kartu. Tidak ada satu pun yang saya simpan.
Mohon dipecah ..." — SEBUT jumlahnya, nol potong diam-diam.

### M5 — dua nama beda, HARGA JUAL SAMA
HIJAU: 2 baris berbeda (Merah / Biru), nama masing-masing BENAR,
`[RESOLVE_ITEM]` = 0 -> nol resolusi silang.

### D3 — parsial & gagal-total (di luar M1–M5, diuji tambahan)
- parsial: "1 dari 3 barang berhasil didaftarkan: Kaos Uji T144 Gamma
  (baris 2). Gagal: baris 1 (...) — Item with this name already exists;
  baris 3 (...) — Item with this name already exists."
- gagal-total: "Tidak ada satu pun dari 5 barang yang berhasil
  didaftarkan. Gagal: baris 1..5 — Item with this name already exists."

## HIJAU-TETAP-HIJAU (sidecar vs milkyhoop.com, stimulus IDENTIK)
| gate | hasil | beda sidecar vs live |
|---|---|---|
| G1 satu barang | kartu `Kaos Uji T144 Tunggal` / persediaan / pcs / Rp 95.000 / Rp 55.000, `items` absen, `render_target=inline` | NOL BEDA |
| G2 create_bill 2 baris | 2 elemen; Dr Persediaan / Cr Hutang **650.000** | NOL BEDA |
| G3 create_quote 2 baris | 2 elemen; 400.000 + 250.000 = **650.000** | NOL BEDA |
| G4 create_customer | kartu 4 field utuh | NOL BEDA |
| G5 kalimat non-pendaftaran | `TEXT`, `pending_action_id=null` | NOL BEDA |

Cacat pra-ada G3 (`"description":"Item"`, `quote_number` berisi kalimat
user) ADA di KEDUA lapisan — comparator TIDAK memburuk, TIDAK diperbaiki
diam-diam (T165/T127).

## Bentuk master `kaos-biru-konveksi`
| tabel | sebelum | sesudah (pra-bersih) | sesudah bersih |
|---|---|---|---|
| products | 2 | 10 | 10 (8 ber-`deleted_at`) |
| journal_entries | 11 | 11 | 11 |
| sales_invoices | 1 | 1 | 1 |
| inventory_ledger | 3 | 3 | 3 |

Pembersihan: soft-delete `deleted_at=NOW(), status='inactive'` + `RETURNING`
(8 baris) + KONTROL NEGATIF (jalankan ulang -> **0 baris**, `UPDATE 0`).

⚠️ `BRG-0003` / `BRG-0004` yang DILARANG DISENTUH ada di tenant
**grapgrap-manado** dan TIDAK tersentuh. Kode `BRG-0003`/`BRG-0004` yang
muncul di sini adalah baris BARU milik `kaos-biru-konveksi` (nomor otomatis
per tenant), lahir dan mati di dalam uji ini.
