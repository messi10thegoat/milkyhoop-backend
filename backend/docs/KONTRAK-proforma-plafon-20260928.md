# KONTRAK — plafon tagihan proforma, PDF proforma, "Sudah Dibayar" (28 Sep 2026)

Putusan MASTER (atas nama pemilik) 28 Sep 2026. SATU atribusi: `services/proforma_atribusi.py`.

## Atribusi uang muka SO ke proforma
1. Uang muka ber-`proforma_id` = milik proforma itu (**tautan menang**).
2. Uang muka TANPA `proforma_id` (urut dibuat) dipasangkan ke proforma **issued** SO yang sama (urut issued_at, nomor)
   yang **sisa terbukanya (amount − tertaut) PERSIS sama nominalnya**. Tiap uang muka & proforma dipasangkan ≤ 1 kali.
3. Sisanya = **uang muka di luar tagihan** (`received_not_billed`).
Uang muka dihitung bila status BUKAN `void`/`draft` (draf = belum diterima).

> ⚠️ Langkah 2 = **heuristik untuk data TAK TERTAUT** (terukur: grapgrap menautkan 2 dari 44 uang muka). Perbaikan
> sejati = MENAUTKAN saat uang muka dicatat — **tiket FE/WORKSPACE**: form uang muka memilih-dulu proforma terbuka yang
> nominalnya cocok. Tak ada backfill tautan di DB (putusan pemilik, tiket atribusi "dibayar" yang diparkir).

## Plafon (pagar create/ubah/terbit + GET /api/sales-orders/{id}/proformas)
`billable_remaining = max(0, order_total − issued_total − received_not_billed)`
GET menambah `billable_breakdown {order_total, issued_total, received_total, received_not_billed}`.
400 menyebut: nilai SO, sudah ditagih (issued), uang muka diterima (di luar tagihan), sisa yang bisa ditagih.

## PDF proforma
`billed_before` = Σ proforma issued LAIN; `received_before` = uang muka SO − yang diatribusikan ke proforma INI;
`remaining_after_this = max(0, order_total − (billed_before + amount) − received_not_billed)`.
Judul ikut tujuan (Uang Muka / Termin / Pelunasan yang Ditagih). PELUNASAN: "Uang muka sudah diterima −Rp X" +
"Sisa setelah tagihan ini". Baris "Pelunasan setelah …" hanya bila tujuan ≠ PELUNASAN dan sisa > 0.

## "Sudah Dibayar" (services/proforma_terbayar)
Eksplisit per proforma = tertaut + tercocok. Kolam "tertutup SO" (turunan jurnal) dikurangi uang muka di luar tagihan
sebelum dialirkan ke proforma → proforma yang ditagih NETO dari uang muka tak tampil dibayar oleh uang yang sama.

## Bukti (28 Sep)
Fixture nyata: grapgrap 019-09-26 → 0, SO-2609-0001 → 1.670.000, 009-08-26 → 22.040.000; kaos SO-2609-0262 → 0;
repro (DP 2 jt lalu PELUNASAN neto 3 jt) → 0 & "Sudah Dibayar" 0; pengetatan DP 2 jt tanpa proforma di SO 5 jt → 3 jt.
Regresi 254 SO: grapgrap 0 berbeda; kaos 29 berbeda (semua pengetatan, SO ber-DP tanpa proforma issued), 0 membesar,
0 negatif. Sabotase merah: max(), tanpa pencocokan, tautan diabaikan, kolam tak dikurangi, draf dihitung.

## 11 Okt 2026 — nominal rupiah, kelebihan bayar, pindah uang muka (Pergatsi; putusan pemilik lewat MASTER, pola NetSuite)

Latar: SO-2610-0015 grapgrap. DP diketik **5.000.000** di Penawaran; `resolve_dp` menurunkan persen TAMPILAN 62.31; form
proforma mendahulukan persen SO; BE menghitung ulang `round(total*persen/100.0, 2)` (float) → PRO-2610-0016 **5.000.377,50**;
uang muka 5.000.000 → sisa 377,50 → posisi "Menunggu DP" (predikat `so_posisi`: paid < amount, tanpa toleransi — tetap begitu).

**Nominal = sumber kebenaran, IDR rupiah penuh.**
- `services/default_dokumen.nominal_dari_persen(total, persen)` = SATU rumus persen→nominal (Decimal, HALF_UP ke rupiah) untuk DP SO
  (`hitung_dp`) DAN proforma (buat + ubah). Nominal yang DIKETIK di proforma juga dibulatkan ke rupiah (`proformas.rupiah`).
- Konversi Penawaran→SO mengisi `sales_orders.dp_amount_source = 'manual'` bila Penawaran punya `dp_amount` (aturan Penawaran:
  nominal mengikat, persen turunan). Dulu NULL.

**Kontrak respons proforma (P2)** — `GET /api/proformas`, `GET /api/proformas/{id}`, `GET /api/sales-orders/{id}/proformas`,
respons buat/ubah/terbit/batal, dan data PDF; satu fungsi murni `services/proforma_terbayar.bayar_proforma`:

| medan | arti |
|---|---|
| `paid_amount` | `min(terbayar, amount)` |
| `outstanding_amount` | `max(0, amount − terbayar)` — tak pernah negatif |
| `excess_amount` (BARU) | `max(0, terbayar − amount)` — kelebihan bayar = tetap uang muka PESANAN (tak ditolak, tak "milik" proforma) |
| `is_fully_paid` | `terbayar + 0,005 ≥ amount` (tak berubah) |

`paid_breakdown` tak berubah. Uang muka melebihi sisa proforma DITERIMA (pagar uang muka = total SO, tak berubah).

**Pindah uang muka ke proforma lain (P1)** — padanan NetSuite unapply/re-apply Customer Deposit (tanpa membalik deposit):
- `POST /api/customer-deposits/{id}/move-proforma` dan `.../move-proforma/preview`, badan `{target_proforma_id, reason?}`, izin `customer_deposit:P`.
- Syarat (blok bila tidak): uang muka `draft|posted|partial` (`DP_TIDAK_BISA_DIPINDAH`); terkait SO (`DP_TANPA_PESANAN`); tujuan ada di
  tenant (`PROFORMA_TIDAK_VALID`), `issued` (`PROFORMA_BUKAN_TERBIT`), SO sama (`PROFORMA_BEDA_PESANAN`), pelanggan sama
  (`PROFORMA_BEDA_PELANGGAN`). Kelebihan atas sisa tujuan BOLEH (→ `excess_amount`).
- Satu transaksi + kunci `PROFORMA_SO:{tenant}:{so}` (mutex dgn buat/terbit/batal proforma) + `FOR UPDATE` baris uang muka;
  hanya `UPDATE customer_deposits SET proforma_id` (cek-lalu-ubah) + riwayat `DEPOSIT_PROFORMA_MOVED`. **NOL jurnal, NOL mutasi bank.**
- Tujuan = tautan sekarang → `no_change: true` (idempoten). Respons: `deposit_*`, `from_proforma_*`, `to_proforma_*`, `can_proceed`,
  `no_change`, `blocks[]`, `proformas[{proforma_id, before, after}]` (bentuk P2), `journals: []`, `bank: null`, `preview`.
- Uang muka TANPA tautan tetap tak melunasi proforma (kecuali nominalnya persis sama sisa) — sengaja, seperti NetSuite: FE
  "Terima DP" wajib mengirim `proforma_id` (putusan MASTER 11 Okt, S2).

**Cek harian** Check 22 (WARNING): proforma `issued` bernominal pecahan rupiah; tanpa daftar kecualian.
Bukti: `scripts/ops/uji-cek-harian-proforma-pecahan.sh`.

**Bukti (11 Okt)**: `tests/unit/test_dp_nominal_rupiah.py` (29; kode master: 28 gagal) + harness nyata kaos
`scripts/ops/uji-nyata-dp-nominal.py` (handler asli, semua ROLLBACK): worktree 16/16; kode master 5 gagal tepat di S3/S4/S5/S6/S7;
S6 = tiruan Pergatsi diperbaiki lewat terbit baru → pindah → batal lama, je/jl/bank_tx + isi baris jurnal identik.

**Koreksi nominal proforma TERBIT** (putusan pemilik lewat MASTER 11 Okt; pola SAP down-payment request — dokumen permintaan
non-posting boleh dikoreksi dengan jejak). Alasan: untuk Pergatsi urutan "terbit baru → pindah → batal" DITOLAK plafon
(8.025.000 − 5.000.377,50 = 3.024.622,50 < 5.000.000; terukur pada tiruan proporsional), dan proforma lama tak bisa dibatalkan
selama uang muka menunjuknya.
- `POST /api/proformas/{id}/correct-amount` dan `.../correct-amount/preview`, badan `{amount, reason}`.
- Izin: rute `proforma:U` + handler menuntut `proforma:C` (= izin batal/terbit), gagal tertutup → tak lebih longgar dari batal.
- Blok: `PROFORMA_NOT_ISSUED` (400), `CORRECT_REASON_REQUIRED` (422, alasan WAJIB), `PROFORMA_AMOUNT_INVALID` (400),
  `PROFORMA_EXCEEDS_BILLABLE` (pagar plafon yang sama, proforma INI dikecualikan). Nominal di bawah terbayar BOLEH (→ `excess_amount`).
- Nominal dibulatkan ke rupiah; `percent_of_order` dikosongkan (nominal = sumber). Satu tx + kunci `PROFORMA_SO`; `UPDATE proformas`
  cek-lalu-ubah + riwayat `PROFORMA_AMOUNT_CORRECTED` "Nominal dikoreksi Rp X → Rp Y: <alasan>". NOL jurnal. Nominal sama → `no_change`.
- Respons: `{proforma_id, proforma_number, status_now, can_proceed, no_change, blocks[], impact{amount_before, amount_after,
  before{amount, paid_amount, outstanding_amount, excess_amount, is_fully_paid}, after{…}}, journals: [], preview, data}`
  (`data` = proforma ter-serialisasi, hanya aksi). PDF & detail memakai nominal baru.
- Pergatsi = SATU langkah: koreksi PRO-2610-0016 5.000.377,50 → 5.000.000 (izin LANGSUNG pemilik, sesudah live).
- Bukti: harness S9 (tiruan proporsional): pratinjau before/after, md5 baris jurnal identik, audit +1, posisi "Diproses",
  predikat Check 22 merah→hijau, riwayat teks persis, PDF nominal baru, plafon = total boleh / total+1 blok, draf/alasan kosong ditolak,
  ulang no-op. Kode master: S9 gagal.
