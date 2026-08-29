# T174 FASE 1 — Gate: kegagalan parse `items` tidak lagi berhenti diam

Situs: `backend/api_gateway/app/services/unified_agent/orchestrator.py`
- `_t144_normalisasi_items` (blok `except`) — kini MENGEMBALIKAN string mentah yang gagal di-parse
- return `DIRECT_ACTION_PREVIEW` create_item — kartu TETAP terbit, DITAMBAH satu kalimat

Diukur lewat https://milkyhoop.com (bukan :8001/:8002), tenant `kaos-biru-konveksi`,
sesi BARU tiap probe, prefix "Kaos Uji T176".

## M1 — MERAH (sebelum patch, master c4310ff5)

Pesan:
`Daftarkan Kaos Uji T176 A dan Kaos Uji T176 B dan Kaos Uji T176 C, harga jual 100000 harga beli 60000`

[LOG] bukti berada di jalur yang benar (container `milkyhoop-dev-api_gateway`):
```
[T144_BULK] items string gagal di-parse: err=Expecting value: line 1 column 1 (char 0) len=32 head='Kaos Uji T176 B, Kaos Uji T176 C'
```

[HTTP] balasan APA ADANYA (`text`), NOL kalimat — B dan C menguap tanpa jejak di layar:
```
### Buat Barang/Jasa

| Field | Value |
|-------|-------|
| Nama | Kaos Uji T176 A |
| Tipe | persediaan |
| Satuan | pcs |
| Harga Jual | Rp 100.000 |
| Harga Beli | Rp 60.000 |
```
message_type=DIRECT_ACTION_PREVIEW  pending_action_id=59f15dfd-640b-4fd1-9565-5ef945433af4
sesi=7746ac94-7c3d-4cc5-a155-f4b93765f867

Gate merah terpicu pada percobaan PERTAMA (Fase 0 mencatat 2/4).

## HIJAU — diisi setelah deploy (lihat bagian bawah berkas ini)

## Deploy

- master SEBELUM: `c4310ff5`
- commit kode+gate: `3bb4f55f`
- commit perbaikan arity (ditangkap M2): `c42bfd1f`
- master SESUDAH: `c42bfd1f`
- `milkyhoop-dev-api_gateway` StartedAt: `2026-08-29T02:30:55Z` → `2026-08-29T03:22:32Z` → `2026-08-29T03:25:36Z` (healthy)

## M1 — HIJAU (pesan IDENTIK dengan gate merah, lewat milkyhoop.com)

[HTTP] balasan APA ADANYA:
```
⚠️ Pesan ini sepertinya memuat beberapa barang, tapi saya cuma berhasil menyusun satu kartu — periksa namanya baik-baik.
Yang tidak tersusun: «Kaos Uji T176 B, Kaos Uji T176 C»
Kalau memang beberapa, kirim ulang sisanya bernomor (1. … 2. …).

### Buat Barang/Jasa

| Field | Value |
|-------|-------|
| Nama | Kaos Uji T176 A |
| Tipe | persediaan |
| Satuan | pcs |
| Harga Jual | Rp 100.000 |
| Harga Beli | Rp 60.000 |
```
Kartu TETAP terbit (message_type=DIRECT_ACTION_PREVIEW, pending_action_id=51a0f979-…).
[LOG] `[T144_BULK] … head='Kaos Uji T176 B, Kaos Uji T176 C'` — log lama tak disentuh, tetap terbit.

## M2 — KONTROL POSITIF (bentuk BERNOMOR)

⚠️ M2 MENYALA sebagai MERAH pada percobaan pertama: HTTP 500
`ValueError: not enough values to unpack (expected 3, got 2)` — jalur SUKSES
`_t144_normalisasi_items` masih mengembalikan `return bersih, 0`. Yaitu: jalur
BERNOMOR yang paling sering dipakai owner SEMPAT rusak total oleh perubahan ini.
Ditangkap justru oleh kontrol positif, bukan oleh M1. Diperbaiki di `c42bfd1f`.

Setelah perbaikan:
```
Ada 3 barang di pesan ini. Saya tampilkan satu per satu supaya tiap barang bisa dicek — dan dilewati — sendiri-sendiri.

1. Kaos Uji T176 D — Jual Rp 100.000 · Beli Rp 60.000 · pcs · persediaan
2. Kaos Uji T176 E — Jual Rp 110.000 · Beli Rp 65.000 · pcs · persediaan
3. Kaos Uji T176 F — Jual Rp 120.000 · Beli Rp 70.000 · pcs · persediaan

Barang 1 dari 3: **Kaos Uji T176 D**
```
Slide `1 dari 3` utuh, kalimat T174 **TIDAK** muncul. ✓

## M3 — satu barang, NOL kalimat tambahan
```
### Buat Barang/Jasa

| Field | Value |
|-------|-------|
| Nama | Kaos Uji T176 G |
| Tipe | persediaan |
| Satuan | pcs |
| Harga Jual | Rp 90.000 |
| Harga Beli | Rp 50.000 |
```

## M4 — 14 barang bernomor, batas 10 utuh
```
Pesan ini memuat 14 barang sekaligus, sementara saya hanya sanggup memproses 10 barang dalam satu kartu. Tidak ada satu pun yang saya simpan. Mohon dipecah jadi beberapa pesan, masing-masing paling banyak 10 barang.
```
[LOG] `[T144_BULK_BATAS] 14 barang dalam satu pesan (batas 10)`

## M5 — rentetan slide penuh + ringkasan T173, tak berubah
```
Barang 2 dari 3: **Kaos Uji T176 Q**
Barang 3 dari 3: **Kaos Uji T176 R**
Selesai. 3 dari 3 barang dibuat.

**Dibuat**
- ✓ Kaos Uji T176 P
- ✓ Kaos Uji T176 Q
- ✓ Kaos Uji T176 R
```
Nol kalimat T174 di seluruh rentetan.

## PAGAR — DIFF dua ujung (products | pending_actions | journal_entries | inventory_ledger)

| tenant | SEBELUM | SESUDAH |
|---|---|---|
| grapgrap-manado | 6 \| 1320 \| 16 \| 2 | 6 \| 1320 \| 16 \| 2 |
| kaos-biru-konveksi | 47 \| 1293 \| 11 \| 3 | 50 \| 1301 \| 11 \| 3 |

grapgrap: NOL BEDA di keempat kolom, dua ujung.
kaos: products +3 (P/Q/R), pending_actions +8 (delapan kartu probe),
**journal_entries +0, inventory_ledger +0** — create_item memang tak berjurnal.

Kontrol positif pengukur (non-destruktif): total lintas tenant
`journal_entries=27` (=16+11) dan `inventory_ledger=5` (=2+3) — predikat tenant
membedakan dan penghitungnya tidak beku pada nol; kolom `products` pada
STATEMENT YANG SAMA memang bergerak 47→50.

## Objek lahir & dibersihkan

pending_action yang lahir (8, tenant kaos-biru-konveksi):
`59f15dfd-640b-4fd1-9565-5ef945433af4`, `bedb21e1-cb37-4101-abc6-4d566577b5d9`,
`2933967f-ebc7-4ea5-ac07-1dc308e6c551`, `51a0f979-df60-449e-a1d9-2e36313d2c47`,
`a53686d2-7ff3-406f-ae45-76208fb581a4`, `557c8329-e390-45e2-a92c-f1137bfd1704`,
`196dfb2f-61fc-41e3-8c1f-49bb1f97adab`, `b450e0b5-2b88-4c75-b4ce-50802ac079ed`

products dibersihkan (soft-delete, RETURNING):
```
d8a4da20-1c69-442f-8d11-6e6bf13be1d0|Kaos Uji T176 P
400115b6-1c62-4268-a8f2-c79714d792ef|Kaos Uji T176 Q
f86b2e91-aad7-4ee7-8075-849e9075f034|Kaos Uji T176 R
```
Kontrol negatif (ulang perintah yang sama): `UPDATE 0` / `(0 rows)`.

## YANG TIDAK TERBUKTI

1. **Drill rollback tidak dijalankan.** `git revert c42bfd1f 3bb4f55f` + restart belum
   pernah dieksekusi, jadi klaim "gejala lama kembali" TIDAK terbukti secara langsung.
   Yang ADA: diferensial perilaku pada pesan IDENTIK sebelum vs sesudah deploy
   (nol kalimat → kalimat + string mentah), pada situs yang sama.
2. **Kontrol positif pengukur jurnal/ledger bersifat lemah.** Percobaan
   DELETE-lalu-ROLLBACK ditolak (benar — itu produksi). Yang dipakai adalah
   perbandingan lintas-tenant; ia membuktikan penghitungnya hidup, BUKAN bahwa ia
   akan menyala untuk baris jurnal yang lahir dari jalur create_item.
3. **Diukur lewat HTTP, bukan mata di browser.** Probe memakai UA browser +
   Origin/Referer lewat https://milkyhoop.com dan membaca field `text` balasan.
   Bagaimana FE MERENDER kalimat ini (mis. apakah `«…»` dan baris baru tampil utuh
   di atas kartu) TIDAK diverifikasi di layar.
4. **Frekuensi kegagalan tak diukur ulang.** Gate merah terpicu pada percobaan
   PERTAMA; angka 2/4 dari Fase 0 tidak dikonfirmasi ulang.
5. Situs `orchestrator.py:4165` (normalisasi KEDUA, hasilnya dibuang) tidak diubah;
   ia tetap menerbitkan baris `[T144_BULK]` kedua di log. Tak berdampak ke layar.
