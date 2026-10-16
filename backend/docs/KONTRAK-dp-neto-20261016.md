# KONTRAK — Uang muka pesanan NETO (tiket RENDAH "batas DP neto setelah refund") — 16 Okt 2026

Status: **UKUR + KONTRAK** (BACKEND, perintah MASTER). Nol tulis DB, nol kode. Ini aturan UANG → kode hanya sesudah
**GO pemilik langsung**. Angka diukur 16 Okt 2026 dari kode berjalan `b65747ad` (read-only, transaksi ROLLBACK).
Hukum yang berlaku: **Law 1 / 16 / 29** (angka uang turunan jurnal, bukan `SUM(wrapper)`), **Law 9** (Decimal,
deterministik), **Law 2** (refund = jurnal baru, deposit tak diubah), **Law 34** (klaim dokumen = hipotesis).

---

## 1. Temuan: EMPAT definisi "uang muka diterima" untuk satu pesanan

| # | Fungsi | Rumus | Atribusi ke SO | Draf | Refund | Dipakai di |
|---|---|---|---|---|---|---|
| A | `customer_deposits.received_total_for_order` | `SUM(amount)` tabel | `sales_order_id` saja | **ikut** | **ikut (bruto)** | (1) batas bawah ubah SO terkonfirmasi `so_ubah_terkonfirmasi.batas_bawah_total`; (2) plafon terima uang muka/pelunasan per SO (`customer_deposits.py:921`); (3) PDF kwitansi "Sisa Nilai Pesanan" (`:3489`) |
| B | `proformas.deposit_totals_for_order` | `SUM(amount)` tabel | `sales_order_id` saja | ikut | ikut | GET `/sales-orders/{id}/proformas` `received_total` / `unbilled_received` |
| C | `proforma_atribusi.muat_atribusi` (`diterima`, `tak_tertagih`) | `amount` per uang muka | SO **atau** lewat proforma | tidak | **ikut (bruto)** | plafon tagih proforma `rincian_tagih`, `tertagih_proforma` di batas bawah ubah SO, terbayar proforma |
| D | `proforma_terbayar.ringkasan_pesanan` | **jurnal** akun CUSTOMER_DEPOSIT_LIABILITY: terima / terap / lain(=refund) + sisa `compute_deposit_remaining_many` | SO atau lewat proforma | tidak (tanpa jurnal) | **neto** | Sisa SO (daftar, Posisi, payment_summary, Rekap) |

A–C membaca kolom `amount` (wrapper) — melanggar Law 1/16/29 dan berbeda dari D yang sudah turunan jurnal. Ini
kelas "kisi dua sumber" (memori 23 Sep): layar Sisa (D) dan pagar (A/C) bisa tak sepakat.

## 2. Ukuran nyata (read-only)

| Tenant | Uang muka per status | Ada refund | Draf ber-SO | SO ber-DP | A ≠ D-neto | A ≠ C | batas berubah bila neto |
|---|---|---|---|---|---|---|---|
| grapgrap | applied 54, posted 21, void 5 | **0** | 0 | 79 | 0 | 0 | 0 |
| kaos | applied 42, partial 1, posted 25, draft 1, void 74 | **1** | 1 | 65 | 2 | 1 | 0 (lihat C) |

Kasus kaos (data uji):
- **SO-2609-0331** (total 400.000): uang muka 50.000, **refund 10.000** → neto 40.000. A = 50.000 (batas bawah ubah SO
  diikat "uang muka diterima" 50.000 → terlalu ketat 10.000, arah aman). **Plafon terima**: sisa yang masih dapat
  diterima = 400.000 − 50.000 = **350.000**, padahal uang yang masih boleh masuk = 360.000 → **pembayaran pesanan
  sebesar sisa penuh DITOLAK** (uang muka/pelunasan SEBELUM faktur; galat "melebihi sisa"; pembayaran FAKTUR lewat
  penerimaan tak memakai plafon ini). PDF kwitansi mencetak Sisa Nilai Pesanan 350.000 (salah 10.000).
  Mengganti A saja TIDAK cukup: C (bruto) membuat `tertagih_proforma` tetap 50.000 → batas tetap 50.000.
- **SO-2609-0003** (total 270.000): uang muka **draf** 1.000 ikut A (163.000) padahal C/D = 162.000. Draf belum uang
  masuk (tanpa jurnal). Batas tak berubah (diikat proforma terbit).
- grapgrap: nol refund, nol draf → **tak ada selisih hari ini**; risiko muncul pada refund/draf pertama.

Jalur lain yang DIUKUR tak mengubah "diterima": pindah uang muka (`move-proforma`) hanya mengganti `proforma_id`
dalam SO & pelanggan yang sama (nol jurnal) → hanya atribusi C; koreksi proforma mengubah tagihan (`issued_total`),
bukan uang diterima.

## 3. Acuan NetSuite / SAP

| Konsep | NetSuite | SAP S/4HANA | MilkyHoop |
|---|---|---|---|
| Uang muka atas pesanan | **Customer Deposit** (record sendiri, menunjuk Sales Order) | Down payment (F-29) atas down payment request / billing plan SO | `customer_deposits` + jurnal CUSTOMER_DEPOSIT |
| Dipakai ke faktur | **Deposit Application** (transaksi terpisah) | clearing down payment saat billing (F-39 / final invoice) | `customer_deposit_applications` + jurnal DEPOSIT_APPLICATION |
| Dikembalikan | **Customer Refund** yang menunjuk deposit (transaksi terpisah; record deposit tak diubah) | pembayaran keluar / pembalikan down payment | jurnal DEPOSIT_REFUND (`refund_deposit_core`), `amount_refunded` cache |
| Saldo deposit | amount − applied − refunded (sisa belum diterapkan) | saldo akun down payment (special G/L) per dokumen | `compute_deposit_remaining_many` (jurnal) |
| Uang yang mengikat pesanan | deposit **dikurangi refund** (uang yang sudah keluar tak lagi milik pesanan) | down payment yang belum dikembalikan | **usul: neto jurnal (D)** |

Prinsip yang diambil: deposit, penerapan, dan refund = transaksi terpisah; record deposit tak diubah (Law 2);
angka pesanan = turunan transaksi (Law 1/16), refund mengurangi uang yang mengikat pesanan.

## 4. Usul (sesudah GO pemilik)

### 4.1 Satu fungsi (satu sumber)
`services/proforma_terbayar.uang_muka_pesanan(conn, tenant_id, so_ids) -> {so_id: {diterima, dikembalikan, neto,
diterapkan, sisa, draf}}` — dibangun dari kueri D yang sudah ada (jurnal akun CUSTOMER_DEPOSIT_LIABILITY,
`is_effective_journal`, atribusi SO atau lewat proforma, void dikecualikan). `neto = diterima − dikembalikan`.
`draf = SUM(amount)` uang muka draf ber-SO (janji, BUKAN uang; dilaporkan terpisah).

### 4.2 Pemakai dialihkan (tiap pemakai = uji lama vs baru + pasangan)
| Pemakai | Sekarang | Usul |
|---|---|---|
| Batas bawah ubah SO (`uang_muka_diterima`) | A bruto + draf | `neto` |
| Batas bawah ubah SO (`tertagih_proforma` via C) | C bruto | C memakai neto per uang muka (`amount − dikembalikan` jurnal) |
| Plafon terima uang muka/pelunasan | total − A | total − (`neto` + `draf`) — draf tetap menahan plafon (janji yang belum diposting; posting tetap dicek ulang) |
| PDF kwitansi "Sisa Nilai Pesanan" | total − A | total − `neto` (posted saja) |
| Panel proforma `received_total`/`unbilled_received` (B) | bruto + draf | `neto` / neto tanpa proforma |
| Terbayar proforma / plafon tagih (C) | bruto | neto per uang muka — **efek uang: proforma yang dibayar uang muka yang lalu direfund tak lagi tampil "lunas"** (putusan pemilik, lihat P2) |

`received_total_for_order` dan `deposit_totals_for_order` menjadi pembungkus tipis `uang_muka_pesanan` (nama lama
tetap, agar pemanggil & tes tak berubah), lalu dihapus di tiket rapih terpisah.

### 4.3 Tanpa migrasi
Semua data sudah ada (jurnal + `customer_deposits`). Nol kolom baru, nol backfill.

### 4.4 Gerbang
- Unit pasangan: untuk tiap SO, `neto == ringkasan_pesanan.dp_received − dp_refunded`; `received_total_for_order ==
  neto` (sesudah alih); plafon + batas + kwitansi memakai SATU fungsi (pemindai sumber: nol `SUM(amount)` uang muka
  tersisa di jalur itu).
- Harness nyata kaos + grapgrap (read-only): grapgrap lama == baru (nol refund/draf); kaos SO-2609-0331: plafon
  360.000, batas 40.000 (bila P2 = neto juga di C), kwitansi Sisa 360.000; SO-2609-0003 draf tak ikut "diterima".
- Lengan tulis kaos ('TES E2E', savepoint ROLLBACK): uang muka → refund sebagian → pelunasan sisa penuh DITERIMA
  (sekarang ditolak) → ubah SO turun sampai neto DITERIMA, di bawahnya 409.
- Kontrol merah: kembalikan A bruto → pasangan & lengan tulis GAGAL.

## 5. Pertanyaan untuk pemilik (aturan uang)
- **P1** GO: "uang muka diterima" untuk pesanan = **neto setelah refund, dari jurnal** (batas ubah SO, plafon terima,
  kwitansi, panel proforma)?
- **P2** Proforma yang dibayar uang muka lalu uang mukanya direfund: dihitung terbayar **neto** (tampil belum lunas
  sebesar refund)? Usul: ya (NetSuite: refund mengurangi saldo deposit yang bisa diterapkan). Hari ini 0 kasus grapgrap.
- **P3** Uang muka **draf** di plafon terima: tetap menahan plafon (usul, aman) atau diabaikan sampai diposting?
- **P4** Prioritas: tetap RENDAH (grapgrap 0 kasus) atau naik karena plafon terima MENOLAK pelunasan sah sesudah refund
  sebagian (kasus kaos nyata)? Usul: naik ke SEDANG — gejala pertama di grapgrap = pembayaran pesanan sebesar sisa ditolak sesudah refund sebagian.
