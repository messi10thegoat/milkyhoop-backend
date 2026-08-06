# E2E UI Walkthrough 2026-08-04/06 — hasil + daftar temuan berurut prioritas

**Tenant:** `kaos-biru-konveksi` (fresh, 1 pelanggan / 1 produk / 1 vendor / 1 bank)
**Metode:** owner mengeklik di UI produksi (`milkyhoop.com`); agen memverifikasi DB + log tiap langkah.
**Bundle:** `main.8f12c2eb.js` dari pin `2bd845159` (lihat `2026-08-03-fe-bundle-provenance.md`).

## Hasil: 9/9 langkah LULUS, buku tutup bersih

Rantai penuh cash-to-cash lewat UI nyata: beli → bayar → tawar → pesan → DP → tagih →
apply DP → kirim → lunas.

| Pos penutup | Ekspektasi | Aktual |
|---|---|---|
| Bank | 21.497.500 | **21.497.500** ✅ |
| AR / AP / Uang Muka / Pend. Dimuka / Persediaan / Stok | 0 | **0** ✅ |
| Penjualan | 5.000.000 | **5.000.000** ✅ |
| COGS | 3.500.000 | **3.500.000** ✅ |
| Laba kotor | 1.500.000 | **1.500.000** ✅ |
| journal_entries | ~11 | 13 (2 ekstra = pasangan void bug biaya bank) |
| Baris-vs-header | seimbang | **OK** ✅ |

**Uji silang:** kas +1.497.500 dari 20jt = laba kotor 1.500.000 − biaya bank 2.500. Cocok ke rupiah
lewat dua jalur berbeda (kas vs laba-rugi).

Bank 21.497.500 (bukan 21.500.000 seperti harness) **benar**: 2.500 = biaya transfer yang diuji owner
di langkah 0b, kini tercatat sebagai beban 5-20850.

Urutan direvisi owner di tengah jalan (menambah langkah 0/0b **pembelian nyata** alih-alih stock
adjustment) supaya WAC 35.000 terbentuk benar. Itu keputusan yang menentukan: adjustment tak
menetapkan WAC → COGS akan salah → seluruh angka penutup rusak dan akan dikejar sebagai "bug"
padahal sebabnya cara mengisi stok.

## Nilai yang dibuktikan: UI menemukan yang harness tak bisa

Bug **P0 biaya bank** (di bawah) **tak akan pernah** ketemu lewat harness backend — `run_all.sh`
lulus semua karena tak pernah mengisi field biaya transfer. Yang menemukannya: owner mengetik
Rp2.500 di form.

---

# DAFTAR PRIORITAS

## P0 — sebelum pengguna nyata masuk

### 1. Kunci OpenAI publik di bundle → `2026-08-04-openai-key-public-in-bundle.md`
Paparan **aktif** sejak 24 Juli. Satu-satunya temuan yang kerugiannya bertambah tiap jam dan tak bisa
diperbaiki belakangan (kunci sudah tersebar). Akar arsitektural: FE memanggil OpenAI langsung —
ganti kunci saja akan bocor lagi di build berikutnya.
Urutan: rotasi → `POST /api/voice/transcribe` → hapus `REACT_APP_OPENAI_API_KEY` → guard build.

### 2. Guard stok di jalur pengiriman → `2026-08-04-no-stock-guard-on-ship-paths.md`
Status `[CODE]`, **belum terbukti runtime**. Kalau benar: stok negatif + COGS 0 → laba kotor palsu
100% margin = laporan L/R salah material tanpa sinyal.
**Diperkecil oleh walkthrough:** jalur normal (stok cukup) terbukti SEHAT — COGS 3.500.000 benar,
stok 100→0, nol negatif. Sisa risiko hanya saat stok TIDAK cukup. Butuh skenario harness #3.

## P1 — sebelum onboarding early adopter

### 3. FE tidak menyegarkan setelah mutasi berhasil → `2026-08-06-fe-stale-after-mutation.md`
Terkonfirmasi 3× dalam satu sesi. Risiko **transaksi ganda** di modul uang.

### 4. FE menelan HTTP 500 jadi "tidak ada hasil" → `2026-08-06-fe-swallows-error-as-empty.md`
Mengarahkan pengguna membuat **produk duplikat** → merusak WAC & laporan.

### 5. SO "Ditagih 100%" padahal faktur masih draft → `2026-08-06-so-invoiced-counter-on-draft.md`
Counter turunan maju pada dokumen yang belum diposting.

## P2 — friksi (pengguna mentok, angka tetap benar)

### 6. Friksi UX form → `2026-08-06-ux-friction-quote-dp-numbering.md`
Diskon butuh dua tap · picker rekening tersembunyi di balik toggle · DP tak di-prefill ·
format nomor tak konsisten · label margin ambigu.

### 7. FE tak mengirim `bank_fee_account_id`
Backend sudah aman (fallback + fail-closed, lihat commit `7713b6fa`). Tetap perlu supaya pengguna
bisa memilih akun biaya. **Kerjakan sekalian dengan #3/#4** — satu lapisan FE yang sama.

## P3 — kebersihan

### 8. Log dipenuhi `connection rejected (403 Forbidden)` WebSocket
Bising menutupi galat asli saat diagnosa.

---

# SUDAH DIPERBAIKI SESI INI (terdeploy ke master)

### `f29b694a` — kolom hantu `products.content_unit` (P1)
`GET /api/products/search/kulakan` 500 → **autocomplete item Faktur Pembelian mati total**;
`GET /api/products/barcode/{x}` juga 500. Fix = hapus dari SQL (BUKAN tambah kolom); 13 kolom lain
diaudit, semuanya sah. Bonus: fallback "satuan dari transaksi terakhir" akhirnya tercapai.

### `7713b6fa` — biaya bank hilang dari jurnal (**P0**, integritas ledger)
Baris beban hanya dibuat bila `bank_fee_amount > 0 AND account_id`, tapi kas selalu dikredit termasuk
fee → **Rp2.500 lenyap** (Dr 3.500.000 / Cr 3.502.500). Lolos karena `CHECK` hanya menguji HEADER.
Fix A: resolusi runtime `5-20850`, fail-closed. Fix B: **guard SUM(baris)==header sebelum POSTED**,
menutup 5 titik lain berpola sama. Terbukti dua sisi (hijau saat benar, merah + rollback saat
dilumpuhkan).

### Law 33 diratifikasi → skill `milkyhoop-ironlaws` v3.9 (33/33)
"Alat verifikasi harus dibuktikan bisa BERBICARA sebelum keheningannya dianggap lulus."
3 instance, 3 mekanisme berbeda: BANK_GAP order-by (memeriksa hal yang salah) · zlib-grep PDF
(mustahil menyala) · rsync `--delete` (bisu karena mode alat). Instance keempat muncul di sesi ini:
guard "double-entry drift" milik agen sendiri membaca HEADER → hijau di atas jurnal timpang.
Sudah diperbaiki jadi baris-vs-header + baseline pin, dan dibuktikan bisa merah.

# GUGUR SETELAH DIPERIKSA

- **Prefiks jurnal bentrok** — TIDAK bentrok. Pembelian `PJ-`, penjualan `JV-`, pengiriman `COGS-`,
  pengakuan `RECOG-`, penerimaan `RCV-`, DP `DEP-`/`DA-`. Seri terpisah.
- **`deliveries.py` jalur pengiriman ketiga** — BUKAN. Read-only (4 GET), fasad atas
  `invoice_fulfillments`. Pengiriman tetap dualitas `/ship` vs `/fulfill`.
- **DP tak terbawa ke SO** — jejaknya ADA di layar SO ("UANG MUKA DITERIMA" + "Dari Penawaran").
  Yang kurang hanya prefill angka saat mengetik → turun ke P2.

# UTANG VERIFIKASI (milik agen, bukan produk)

- Gate `BUILD_INFO.json` belum diuji-merah → per Law 33 belum boleh dipercaya.
- Skenario harness #2 (auto-fulfill) & #3 (ship-without-stock) belum ditulis.
- Baseline pin di `/root/wt-check.sh` mengecualikan `BP-2608-0001` + `VD-2608-0001` (net 0,
  permanen karena Law 2). Sengaja SEMPIT & by-name; jangan pernah dilebarkan jadi pola.
