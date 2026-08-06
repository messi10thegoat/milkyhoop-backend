# LAPORAN SESI — Rebuild FE + Walkthrough E2E UI

**Periode:** 2026-08-04 s/d 06 · **Tenant:** `kaos-biru-konveksi` · **Server:** 159.89.202.160
**Temuan berurut prioritas:** lihat `DOCS/issues/2026-08-06-e2e-walkthrough-findings-index.md`

## 1. Ringkasan eksekutif

Dua capaian: (a) celah provenance frontend ditutup — bundle live kini terlacak ke commit sumber dan
terverifikasi dari edge; (b) seluruh siklus bisnis cash-to-cash divalidasi lewat UI nyata oleh
manusia, bukan curl/harness. **9/9 langkah lulus, buku tutup cocok ke rupiah.**

Tiga perbaikan backend dilakukan di tengah jalan, satu di antaranya **P0 integritas ledger**.

**Temuan struktural terpenting:** harness backend yang hijau total tetap bisa menyembunyikan
kerusakan pembukuan. `run_all.sh` lulus 100% karena tak pernah mengisi field biaya transfer. Yang
membongkarnya: owner mengetik Rp2.500 untuk "sekalian nyoba field tersebut".

## 2. Yang berhasil

### 2.1 Provenance FE
Bundle `main.8f12c2eb.js` dibangun dari pin `2bd845159` (tree bersih), verifikasi tiga titik cocok
(container = origin :3001 = edge), `BUILD_INFO.json` terbaca dari edge, rollback siap sebelum deploy
(`/root/fe-rollback.sh`), `api_gateway` tak tersentuh saat itu.
Temuan sampingan: **purge Cloudflare tidak diperlukan** — `index.html` + `BUILD_INFO.json` DYNAMIC,
aset lain ber-hash konten.

### 2.2 Siklus bisnis 9/9

| # | Langkah | Jurnal | Hasil |
|---|---|---|---|
| 0 | Faktur Pembelian 100 @35.000 | `PJ-2608-0001` | Persediaan 3.500.000, WAC 35.000 |
| 0b | Pembayaran Keluar + biaya 2.500 | `BP-2608-0003` | 3 baris seimbang, AP 0 |
| 1 | Penawaran + DP 30% + rekening | — | 5.000.000, DP 1.500.000, 3 field bank |
| 2 | Konversi + konfirmasi SO | — | nol jurnal (benar) |
| 3 | Terima DP | `DEP-2608-0001` | Dr Bank / Cr Uang Muka, nol sentuh AR |
| 4 | Faktur dari SO | `JV-2608-0001` | Dr Piutang / Cr Pend. Diterima Dimuka |
| 5 | Apply DP | `DA-2608-0001` | AR 5.000.000 -> 3.500.000 |
| 6 | Pengiriman | `COGS-` + `RECOG-` | stok 0, COGS 3.500.000, revenue diakui |
| 7 | Pelunasan | `RCV-2608-0001` | AR 0, faktur `paid` |

### 2.3 Neraca penutup
bank 21.497.500 · AR/AP/2-10500/2-10750/persediaan/stok = 0 · penjualan 5.000.000 ·
COGS 3.500.000 · laba kotor 1.500.000 · chain 13/13 · drift 0 · baris-vs-header OK.

**Uji silang:** kas +1.497.500 = laba kotor 1.500.000 - biaya bank 2.500. Cocok ke rupiah lewat dua
jalur independen (arus kas vs laba-rugi).

### 2.4 Iron Laws terbukti runtime
Law 29/30 (jurnal DP nol menyentuh RECEIVABLE) · PSAK 72 mode `delivery` (revenue tertahan di
2-10750 sampai pengiriman) · Law 16 (angka layar = journal-derived) · Law 2 (void meninggalkan asli
POSTED).

## 3. Yang diperbaiki

- **`7713b6fa` P0** — biaya bank hilang dari jurnal. Akar: baris beban hanya dibuat bila
  `amount>0 AND account_id`, tapi kas selalu dikredit termasuk fee. Lolos karena `je_balanced CHECK`
  hanya menguji header. Fix A: resolve runtime `5-20850` fail-closed. Fix B: guard
  `SUM(baris)==header` sebelum POSTED. **Cakupan Fix B masih 1 dari 94 jalur** — lihat
  `2026-08-06-guard-line-header-scope.md`.
- **`f29b694a` P1** — kolom hantu `products.content_unit`; autocomplete Faktur Pembelian mati total.
- **Law 33 diratifikasi** -> skill `milkyhoop-ironlaws` v3.9 (33/33).

## 4. Keadaan repo & runtime (per 2026-08-06)

```
master                 = 7713b6fa (memuat kedua fix, sinkron dengan deploy/master)
docs/fe-provenance-rebuild = 4 commit docs, BELUM di-merge (sengaja; docs-only)
main tree backend      = bersih (0 file dirty)
kode di container      = master (marker guard=1, ghost column=0)
api_gateway StartedAt  = 2026-08-05T09:57:33Z (2x restart manual: fix kulakan, fix bankfee)
frontend StartedAt     = 2026-08-04T09:42:14Z
schema_migrations      = 214 (nol migration ditambah sesi ini)
```
Nol perubahan lain ikut ter-deploy. Kedua deploy backend dilakukan atas GO owner eksplisit,
di luar runbook batch tapi dengan owner di depan layar.

## 5. Gate provenance: SUDAH diuji-merah (utang lunas)

`/root/check_build_info.sh <base_url> <expected_sha>` — 1 hijau, 4 merah, semuanya dijalankan:

| Skenario | Hasil |
|---|---|
| produksi nyata, sha benar | **HIJAU** exit 0 |
| sha diharap salah | MERAH exit 1 |
| **artefak ter-deploy ber-sha salah** (nginx sekali-pakai) | **MERAH** exit 1 |
| BUILD_INFO tidak ada | MERAH exit 1 |
| `tree_clean=false` | MERAH exit 1 |

Skenario ketiga adalah uji yang sesungguhnya: mensimulasikan deploy salah tanpa menyentuh produksi.

## 6. Yang tertinggal
Lihat `DOCS/issues/2026-08-06-e2e-walkthrough-findings-index.md` (berurut prioritas).
Ringkas: P0 kunci OpenAI · P0 cakupan guard 1/94 · P0 guard stok (belum runtime) ·
P1 FE stale-after-mutation · P1 FE menelan 500 · P1 SO counter pada draft · P2 friksi UX.
