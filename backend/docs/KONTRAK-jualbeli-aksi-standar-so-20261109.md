# KONTRAK gelombang 3 — celah S lintas modul + aksi_tersedia detail jual/beli (BACKEND, 9 Nov 2026)

Status: **§5 DIPUTUS MASTER 9 Nov** (diukur read-only di master 7dac48377). Kode JUAL dimulai setelah lampiran-1 live dan kunci aksi dari WORKSPACE tiba.

Tujuan: pill detail semua modul jual/beli dari SERVER seperti Kontak / Kas & Bank / Persediaan (`aksi_tersedia` = `{kunci: {tersedia, alasan}}`, bentuk bersama `schemas/aksi_tersedia`), plus celah S daftar.

## 0. Hasil ukur (grep router + skema, 14 modul)

| Modul | aksi_semua daftar | aksi_tersedia detail | position_text | status_text |
|---|---|---|---|---|
| SO | ✅ | ✖ | ✅ | – |
| Penawaran | ✖ | ✖ | ✅ | – |
| Proforma | ◐ router saja (skema ✖) | ✖ | ✅ | – |
| Faktur penjualan | ✖ | ✖ | ✅ | – |
| Penerimaan (RP) | ✖ | ✖ | ◐ (cek skema) | – |
| Nota kredit | ✖ | ✖ | ✅ | ✅ (alias) |
| UM pelanggan | ✖ | ✖ | ✅ | ✅ |
| Surat jalan | ◐ router saja | ✖ | ✅ | – |
| PO | ✖ | ✖ | ✅ | – |
| Penerimaan barang | ✖ (aksi tunggal di grn_daftar) | ✖ | ✅ | ✅ |
| Faktur pembelian | ◐ skema saja | ✅ **level ATAS** (nota_debit.aksi_tersedia_tagihan) | ✅ | – |
| Pembayaran keluar | ✖ | ✖ | ✅ | – |
| Nota debit | ◐ skema saja | ◐ router | ✅ | ✅ |
| UM vendor | ✖ | ✖ | ✅ | ✅ |

Hampir semua aksi tulis sudah punya rute `…/preview`, yaitu penentu yang SAMA dengan tulis. Contoh: SO 7, penawaran 7, PO 7, NK 5, DP 6, UM vendor 7.

## 1. Celah S daftar (aditif)

- `aksi_semua` di skema + baris daftar SI, PO, RP, BP, NK, GRN, Penawaran, Proforma, SJ, ND, UM pelanggan, UM vendor. Penentunya Python yang SETARA `sql_aksi` modul itu (uji berpasangan, pola biaya_daftar); `aksi` = elemen pertama.
- `position_text` daftar + detail RP, bila skema belum memuatnya.
- `status_text` tetap; `position_text` disediakan di semua modul yang baru punya `status_text` (alias aditif, SATU penentu).

## 2. aksi_tersedia detail (14 modul)

- Kunci = aksi tulis modul itu (urutan = urutan tampil), misalnya SO: `konfirmasi, ubah, kirim, buat_faktur, terima_bayar, tutup, batalkan`.
- `tersedia/alasan` = BLOK PERTAMA penentu pratinjau aksi itu, dijalankan READ ONLY. Aksi yang butuh badan nominal (apply, refund, terima bayar) hanya dinilai dari penjaga status/sisa penentunya; nominal divalidasi saat pratinjau sungguhan.
- Satu helper bersama `services/aksi_detail.py`: `await aksi_dari_pratinjau(conn, [(kunci, rencana_fn), …])` dalam SATU transaksi READ ONLY (rollback), tanpa salinan aturan.

## 3. Ukuran + urutan usul

- L untuk aksi_tersedia: 14 modul × penentu, tapi helper bersama dan penentu pratinjau sudah ada. S untuk §1.
- Urutan (grosir dulu): jual (SO, SI, RP, Penawaran, Proforma, UM pelanggan, NK, SJ), lalu beli (PO, GRN, Bill, BP, ND, UM vendor).
- Satu cabang per kelompok (jual / beli), masing-masing dengan gerbang penuh.

## 4. Bukti

- Unit: setiap kunci aksi_tersedia == hasil pratinjau aksinya (sampel status per modul); pasangan aksi_semua ⇔ sql_aksi; placeholder == argumen.
- Salinan: detail semua dokumen kaos/nyawit; untuk setiap aksi yang `tersedia`, pratinjau aksinya `ok`, dan sebaliknya. Medan lama identik.
- Sabotase, suite, gate. Kinerja: waktu detail sebelum/sesudah per modul di salinan.

## 5. Putusan MASTER 9 Nov

1. **Letak:** aksi_tersedia DI DALAM `data` untuk semua modul. Biaya/Bill/ND diberi alias di dalam `data`; salinan di level atas tetap sampai FE pindah. TIKET: hapus salinan level atas.
2. **Kinerja:** selalu ikut, dengan syarat terukur di salinan.
   - Ukur p50/p95 GET detail lama vs baru untuk SO, Faktur, dan PO.
   - Bila p95 naik >80 ms ATAU >30%, aksi_tersedia jadi opt-in `?aksi_tersedia=1`, lalu dilaporkan.
   - SATU transaksi READ ONLY per permintaan, bukan per aksi.
3. **Kunci aksi:** daftar per modul datang dari WORKSPACE (peta + IZIN_PILL G0a), lewat MASTER. Server TIDAK mengarang nama.

## 6. Kunci aksi (WORKSPACE 9 Nov, lewat MASTER) — sumber tunggal: `KUNCI-aksi-detail-per-modul-20261109.md`

- **Emit HANYA kunci jenis `tulis` / `tulis·kilat`**, dengan nama PERSIS seperti di dokumen itu (putusan MASTER: tidak diseragamkan sekarang; TIKET "seragamkan kunci aksi"). Kunci navigasi (wa, pdf, ubah→form, duplikat, podraf, grndraf, pindah SO, fakturbatal) = urusan FE.
- **Kunci kilat tanpa pratinjau server:**
  - SO: `konfirmasi`, `hapus`, `dp`;
  - Penawaran: `kirim`, `terima`, `tolak`, `perpanjang`, `hapus`;
  - SI: `hapusdraf`, `kirim` tanpa SO.

  Untuk kunci ini aksi_tersedia = penjaga STATUS penentu tulisnya saja (tidak ada pratinjau yang dijalankan).
- **Flag tampilan FE** (mis. `conversational_close_page_so`, fase lokasi, `aksi_pembelian_v1`) tetap disaring FE. Server menilai kelayakan aksi; flag tenant server yang memang menjaga tulis tetap dihormati penentunya.
- **Petunjuk lama tetap utuh:**
  - PO: `terima_tersedia`, `tagih_tersedia`, `draft_bills`, `belum_ditagih_sesudah_draf`;
  - FPB: `aksi_tersedia.nota_debit` di level atas;
  - GRN: `aksi`, `draft_bills`.
- **Satu kunci, dua label** (NK `batal`, ND `ndbatal`): satu entri server; labelnya milik FE.

## 7. J1a — SO irisan vertikal (BACKEND 9 Nov, cabang `feat/jual-aksi-tersedia`)

`GET /api/sales-orders/{id}` → `data.aksi_tersedia`, dengan 15 kunci urut §1 KUNCI: konfirmasi, ubahkepala, batal, hapus, faktur, dp, kirim, bayar, proforma, tutup, terbitfaktur, fasekirim, fasetersedia, faseubah, spk.

Semuanya dinilai di SATU transaksi READ ONLY (`services/aksi_detail.nilai`). Tidak ada penulis+rollback.

### Penentu per kunci

Semuanya penentu tulis yang sama, tanpa salinan aturan.

| kunci | penentu | blok KEADAAN (lainnya = isian, diabaikan) |
|---|---|---|
| konfirmasi | `rencana_konfirmasi_so(kunci=False)` (penentu penulis) | galat 400 status |
| ubahkepala | `so_ubah_terkonfirmasi.penjaga_pratinjau` (diekstrak; dipakai rute edit/preview + `ubah()`) | SO_IS_DRAFT · SO_EDIT_NOT_ENABLED · SO_NOT_EDITABLE |
| batal | `_rencana_batal_so` | semua (tanpa isian) |
| hapus | `rencana_hapus_so` | semua |
| faktur | `penjaga_tagih_so` (diekstrak dari `_buat_faktur_dari_so`) + `SQL_BARIS_SISA_TAGIH` (SQL penulis yang sama) | status · "Tidak ada barang yang bisa ditagih." |
| dp | `customer_deposits.penjaga_status_dp` (diekstrak dari pagar plafon) + sisa = total − diterima ≤ 0 → `rincian_plafon` (kalimat penulis) | SO_NOT_ACCEPTING_DEPOSIT · plafon habis |
| kirim | `rencana_kirim_so(SOFulfillRequest())` ATAU (flag `pengiriman_sebelum_faktur`) `sj_so.rencana(kunci=False)`; tersedia bila SALAH SATU terbuka | SO_FULFILL_NO_INVOICE · SO_FULFILL_NOTHING_TO_SHIP · SJ_SO_STATUS_TIDAK_BOLEH · SJ_SO_STOK_BELUM_DIDUKUNG · SJ_SO_TANPA_SISA |
| bayar | `rencana_pelunasan_so(SOReceivePaymentPreviewRequest())` (lapis 1 saja) | RP_NO_OPEN_INVOICES · RP_NOTHING_DUE · RP_CUSTOMER_MISSING |
| proforma | `proformas.blok_so_proforma` (diekstrak dari `_rencana_buat`) | SO_NOT_BILLABLE |
| tutup | `_rencana_tutup_so(kunci=False)`; saklar baru, bawaan True = penulis + pratinjau jalur lama | SO_STATUS_NOT_CLOSABLE · SO_REVENUE_NOT_RECOGNIZED (SO_NOT_FULLY_INVOICED = alasan wajib = isian) |
| terbitfaktur | ada faktur draf SO (penjaga POST /sales-invoices/{id}/post = status draf) | "Hanya faktur berstatus Draf yang bisa diterbitkan." |
| fasekirim/fasetersedia/faseubah | `so_fase_lokasi.rencana(None, None)`. Penulis membolehkan maju/mundur/hapus, jadi ketiganya satu putusan server; urutan fase = tampilan FE | SO_STATUS_TIDAK_BOLEH |
| spk | setelan nyala (`pesan_spk_nonaktif`) + `kode_order.penjaga_spk` (diekstrak; dipakai `terbitkan_spk` + `perkiraan_spk`) | SPK_NONAKTIF · SPK_STATUS_TIDAK_SAH |

### Beda dengan "tampil bila" FE (dilaporkan, TIDAK dikarang server)

- `dp`: penulis TIDAK menolak SO yang sudah ditagih (FE: ditagih=0).
- `spk`: SO yang sudah berkode = idempoten. Pratinjau mengembalikan `can_proceed` dan server menilainya tersedia; FE menyembunyikannya bila sudah berkode.
- `proforma`: hanya penjaga status. Plafon proforma bergantung nominal.
- `kirim`: stok kurang / gudang / periode = isian (qty, gudang, dan tanggal bisa diubah).
- `terbitfaktur`: penulis tidak memeriksa status SO (FE: SO bukan draft/batal/selesai).

### Bukti

- Unit `tests/unit/test_so_aksi_tersedia.py`.
- Salinan `scripts/ops/uji-salinan-so-aksi.sh`:
  - detail lama == baru tanpa aksi_tersedia;
  - hasil penulis lama == baru (perilaku TULIS identik, termasuk tutup kunci=True);
  - paritas tersedia ⇔ penulis;
  - kunci yang dipegang transaksi READ ONLY;
  - p50/p95.

## 8. J1b — 7 modul jual (BACKEND 10 Nov, cabang `feat/jual-aksi-tersedia-j1b`)

`data.aksi_tersedia` di detail: Penawaran, Proforma, Faktur, Surat Jalan, Penerimaan, Uang Muka, Nota Kredit.

- Kunci persis KUNCI §2–§8.
- SATU transaksi READ ONLY per GET.
- Penilai sama dengan J1a (`services/aksi_detail`; `alasan_blok(..., kecuali=)` untuk kode isian).

### Penentu per modul

Kode di kurung = isian, diabaikan.

**Penawaran**
- `kirim`: `rencana_kirim_penawaran(kunci=False)`.
- `kirimdok`: `penawaran_bagikan.penentu` (sama dengan share).
- `terima` / `tolak`: `rencana_terima_tolak(kunci=False)`. Alasan tolak = isian.
- `pesanan`: SO hasil konversi ada → tersedia (penulis idempoten already_converted); selain itu `penjaga_konversi_penawaran` (diekstrak; dipakai to-order DAN to-invoice).
- `perpanjang`: `penjaga_ubah_penawaran` (diekstrak dari PATCH; STATUS_BOLEH_UBAH).
- `batal`: `_rencana_void_penawaran` atas baris `_ambil_penawaran_void(kunci=False)` (saklar baru; bawaan True = jalur lama) (ALASAN_WAJIB).
- `hapus`: `rencana_hapus_penawaran`.

**Proforma** (baris dibaca tanpa `_kunci_proforma`)
- `terbit`: `_rencana_terbit`.
- `ubah`: `_rencana_ubah` dengan badan kosong (keadaan PROFORMA_NOT_DRAFT).
- `batal`: `_rencana_batal` (CANCEL_REASON_REQUIRED).
- `koreksi`: `_rencana_koreksi` dengan nominal sama (keadaan PROFORMA_NOT_ISSUED). Flag `koreksi_dp_proforma` = FE; penulis tak memeriksanya.

**Faktur**
- `terbit`: `penjaga_terbit_faktur` (diekstrak dari POST /post). Draf → + `faktur_terbit_rencana.rencana_terbit_ro` (penentu berat hanya untuk draf).
- `hapusdraf`: `penjaga_hapus_faktur` (diekstrak dari DELETE).
- `bayar`: `rencana_pelunasan_faktur` lapis 1 (keadaan RP_INVOICE_DRAFT/VOID, RP_NOTHING_DUE, RP_CUSTOMER_MISSING).
- `kirim`:
  - ber-SO = `alasan_kirim_so` (penentu `kirim` SO yang SAMA, FE menavigasi ke SO);
  - tanpa SO = `penjaga_kirim_faktur` (diekstrak dari POST /fulfill).
- `nk`: `pihak_helpers.penjaga_faktur_nk` (diekstrak dari `pastikan_cn_muat_faktur`). Sisa yang bisa dinotakan = nominal (isian).
- `void`: `_rencana_void_faktur` (penentu baca; pratinjau void tetap penulis+rollback, tak dipakai).

**Surat Jalan**
- `sjbatal`: `sj_so.rencana_void(kunci=False)`, asal SO maupun faktur (SJ_ALASAN_WAJIB). Asal SO tanpa flag → kalimat SJ_SO_NONAKTIF.

**Penerimaan**
- `rpposting` / `rphapus`: `_pratinjau_draf_penerimaan(post|delete)`.
- `batal`: `_rencana_void_pembayaran(kunci=False)`; alasan = isian.

**Uang Muka**
- `terapkan` / `refund`: `blok_status_dp` (diekstrak dari `_rencana_dp`). Faktur/nominal/rekening = isian.
- `lepas`: `_rencana_dp(reverse)` per penerapan aktif; tersedia bila SATU bisa dilepas. Tanpa penerapan aktif = kalimat 404 penulis (`PENERAPAN_DP_TAK_ADA`).
- `batal`: `_rencana_dp(void)` (VOID_REASON_REQUIRED).
- `pindah`: `_rencana_pindah(kunci=False)` (keadaan DP_TIDAK_BISA_DIPINDAH, DP_TANPA_PESANAN).

**Nota Kredit** (semua `pratinjau=True`)
- `terbit`: `rencana_posting_nk`.
- `batal`: `rencana_batal_nk` (VOID_REASON_REQUIRED); draf = dihapus.
- `terapkan`: `rencana_terapkan_nk` dengan aplikasi kosong (keadaan CN_SUDAH_TERKAIT, CN_BELUM_TERBIT, CN_SUDAH_DIKEMBALIKAN).
- `lepas`: `rencana_lepas_nk` (CN_ALASAN_WAJIB).
- `refund`: `blok_refund_nk` (diekstrak).

### Beda dengan "tampil bila" FE (dilaporkan, server = penulis)

- Penawaran `pesanan`: tersedia pada penawaran yang sudah jadi SO (idempoten).
- SJ `sjbatal`: SJ asal SO yang sudah batal = ulang idempoten (tersedia).
- DP / NK `terapkan`: tanpa syarat "ada customer_id".
- Faktur `nk`: tanpa syarat sisa > 0 (nominal).
- Proforma `koreksi` / DP `pindah`: flag FE tak dinilai server.

### Bukti

- Unit `tests/unit/test_jual_aksi_tersedia_j1b.py`.
- Salinan `scripts/ops/uji-salinan-jual-aksi.sh`:
  - 7 modul, detail lama == baru;
  - penulis/pratinjau lama == baru;
  - paritas;
  - pg_locks;
  - p50/p95 per modul;
  - penyimpanan objek TIRUAN.

### Kinerja J1b — putusan MASTER 10 Nov (untuk FE)

Kriteria §5.2 ditafsir ulang (b): **opt-in bila p95 naik >80 ms ATAU (>30% DAN >20 ms)**.

**Selalu ikut** (`data.aksi_tersedia` selalu ada): SO (J1a), Penawaran, Proforma, Surat Jalan, Penerimaan, Uang Muka, Nota Kredit.

**Opt-in:** **Faktur** — `GET /api/sales-invoices/{id}?aksi_tersedia=1`. Tanpa parameter, kunci `aksi_tersedia` TIDAK ada; FE memakai logika lamanya.
- Biaya murni (terselang off/on, klon sama): p95 +28,2 ms / +66%.
- Per kunci (p50/p95 ms): bayar 8,9/15,3 · kirim 6,3/16,9 · terbit 0/6,9 · void 2,3/6,8.
- TIKET terpisah: `compute_ar_outstanding(tenant)` dihitung untuk seluruh tenant lalu disaring per faktur. Ini kinerja penentu pelunasan itu sendiri, penulis juga. BE sedang; butuh ironlaws + uji paritas uang.

**Nota Kredit selalu ikut**, diputus dari data **per kunci**, bukan dari on/off:
- total p50 ±2,5 ms;
- p95 per kunci: terbit 4,3 · batal 4,2 · lepas 1,8 · terapkan 1,2 · refund 0,2;
- on/off +22 ms adalah derau n kecil (run sebelumnya +9,4 ms).

**Belum terukur:** Proforma `koreksi`. Pratinjau memanggil izin PolicyEngine, yang tak ada di kontainer uji. TIKET: PolicyEngine di kontainer uji.

**PUTUSAN 1:** POST /receive-payments/{id}/post memeriksa status draf SEBELUM `_periksa_sisa_draf` (`penjaga_posting_rp`, satu sumber dengan `_post_payment`). Penerimaan non-draf ditolak dengan kalimat status (dulu "Alokasi melebihi sisa"); kelayakan sama.
