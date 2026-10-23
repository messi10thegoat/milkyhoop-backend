# KONTRAK — Dashboard: SATU SUMBER PENGINGAT (T1-BE, 22 Okt 2026, BACKEND2)

Perintah MASTER 22 Okt (temuan WORKSPACE §3d + FRONTEND butir 2). Pola acuan: NetSuite *Reminders* (reminder = saved search yang SAMA +
satu kriteria), QBO *Bills to pay* (AP satu agregat), Xero/QBO "due soon" = jatuh tempo dalam N hari (bawaan 7).

## 0. Keadaan sekarang (diukur 22 Okt, master da575cfd)
- `GET /api/dashboard/tasks` (services/dashboard_v2.susun_tugas) menghitung SENDIRI dari `compute_ar_outstanding`/`compute_ap_outstanding`:
  `ar_due_today` (per faktur), `ar_overdue` (agregat), `ar_due_soon` (besok per faktur + 2–7 hari per hari), `ap_due` (**per tagihan**, ≤ 7 hari
  atau telat), `so_to_ship` (so_aksi telat_kirim/perlu_dikirim/siap_diserahkan + jendela akhir minggu), `so_dp_pending` (**gabungan**
  menunggu_dp ∪ perlu_minta_dp), `bank_recon`. Ringkasan: `sum_now` = Σ amount lajur "now" (campur uang masuk & keluar).
- "Segera" punya TIGA definisi: faktur `segera_tempo` ≤ hari ini + **3** (faktur_aksi.AMBANG_SEGERA_TEMPO, kunci PARTISI pill);
  proforma `segera_tempo` ≤ +**3** (proforma_aksi); faktur & tagihan `jatuh_tempo_minggu_ini` ≤ **akhir minggu** (dashboard_v2.akhir_minggu);
  dashboard `ar_due_soon` ≤ +**7**. (Penawaran `segera_habis` = masa berlaku habis, BUKAN jatuh tempo bayar — di luar definisi ini.)
- Tak ada wadah pengaturan key-value per tenant; pengaturan = kolom `accounting_settings` (mis. `default_dp_percent`).

## 1. Definisi tunggal "segera jatuh tempo"
`segera_tempo(dok)` = TERBUKA (sisa > 0) ∧ hari_ini ≤ jatuh_tempo_efektif ≤ hari_ini + **N**, N = `pengingat_segera_hari` tenant (bawaan **7**).
`jatuh_tempo` / `lewat_jatuh_tempo` = TERBUKA ∧ jatuh_tempo_efektif < hari_ini (partisi: dokumen tak bisa dua-duanya).
- SATU fungsi: `services/pengingat.ambang_segera(conn, tenant) -> int` (baca pengaturan; kosong → 7). Dipakai faktur_aksi, proforma_aksi,
  tagihan_daftar, dashboard.
- **Kunci daftar seragam disatukan**: faktur `segera_tempo` (ambang 3 → N), proforma `segera_tempo` (3 → N), tagihan: kunci baru
  `segera_tempo` (N hari) MENGGANTIKAN `jatuh_tempo_minggu_ini`; faktur `jatuh_tempo_minggu_ini` (chip non-partisi) → alias USANG dari
  `segera_tempo` selama transisi FE (diterima di `?aksi=`, dikirim di summary dgn penanda `usang`), lalu dicabut.
- **Pengaturan N** (perlu MIGRASI + izin pemilik LANGSUNG): kolom `accounting_settings.pengingat_segera_hari smallint NULL CHECK (1..60)`;
  NULL = 7. Sampai migrasi ada: konstanta 7 (perilaku daftar berubah 3 → 7 hari / akhir minggu → 7 hari = PERUBAHAN PERILAKU, butuh izin).

## 2. Daftar pengingat (v1) — SATU registri `services/pengingat.PENGINGAT`
Tiap pengingat = `{kunci_aksi, modul, label, count, amount (string 2dp | null), arah: masuk|keluar|non_uang, izin, list}`. `count`/`amount`
dihitung dengan PREDIKAT SQL YANG SAMA dengan `?aksi=` + `/summary` daftar modul itu (bukan hitung ulang di dashboard):

| modul | kunci_aksi | arah | amount | sumber predikat (sama dgn daftar) | izin |
|---|---|---|---|---|---|
| sales_invoices | `jatuh_tempo` | masuk | Σ sisa (compute_ar_outstanding) | faktur_aksi.sql_aksi | sales_invoice R |
| sales_invoices | `segera_tempo` | masuk | Σ sisa | faktur_aksi.sql_aksi (ambang N) | sales_invoice R |
| proformas | `lewat_tempo` | masuk | Σ sisa proforma | proforma_aksi | proforma R |
| proformas | `segera_tempo` | masuk | Σ sisa proforma | proforma_aksi (ambang N) | proforma R |
| sales_orders | `menunggu_dp` | masuk | Σ DP wajib belum diterima | so_aksi | sales_order R |
| sales_orders | `perlu_minta_dp` | masuk | Σ DP yang perlu diminta | so_aksi | sales_order R |
| sales_orders | `telat_kirim` | non_uang | null | so_aksi | sales_order R |
| sales_orders | `perlu_dikirim` | non_uang | null | so_aksi | sales_order R |
| bills | `lewat_jatuh_tempo` | keluar | Σ sisa (compute_ap_outstanding) — **SATU agregat** (QBO Bills to pay) | tagihan_daftar.sql_aksi | purchase_invoice R |
| bills | `segera_tempo` | keluar | Σ sisa — agregat | tagihan_daftar.sql_aksi (ambang N) | purchase_invoice R |
| purchase_orders | `telat_terima` | non_uang | null | po_daftar.sql_aksi | purchase_order R |
| vendor_deposits | `belum_diterapkan` | keluar* | Σ sisa uang muka | UMD.sql_aksi | vendor_deposit R |

\* uang muka vendor belum diterapkan = dana sudah keluar, belum terpakai; arah `keluar` untuk pengelompokan (bukan rencana bayar) —
**putusan MASTER**: masuk v1 atau tidak. `bank_recon` tetap kartu terpisah (bukan kunci aksi daftar; arah non_uang).
Hanya pengingat dengan `count > 0` dikirim. `menunggu_dp` dan `perlu_minta_dp` = DUA pengingat (dulu digabung `so_dp_pending`).

## 3. Respons `GET /api/dashboard/tasks` (aditif; lama tetap selama transisi)
```
{ as_of, done_today, total_today,
  reminders: [ {key, kunci_aksi, modul, label, count, amount, arah, lane, list:{modul, aksi}} ... ],   // BARU: satu sumber
  sum_in:  "…",   // Σ amount reminders arah=masuk  (string 2dp)
  sum_out: "…",   // Σ amount reminders arah=keluar
  sum_now: "…",   // USANG (kompatibilitas): nilai lama; dicabut sesudah FE pindah
  tasks: [ … ]    // USANG: kartu lama; per-tagihan ap_due DIGANTI satu kartu agregat sejak versi ini
}
```
- `key` pengingat = `"{modul}:{kunci_aksi}:{count}:{amount}"` (dismiss ikut kunci; berubah bila isi berubah, pola kartu lama).
- `lane`: `now` untuk `jatuh_tempo`/`lewat_*`/`telat_*`; `week` untuk `segera_tempo`/`perlu_dikirim`; `any` lainnya.
- `list` = tujuan klik: buka daftar modul dengan `?aksi={kunci_aksi}` → baris yang tampil == count pengingat (paritas).
- Izin: pengingat tanpa izin modul tak dikirim (pola tasks sekarang). Cache 60 dtk tetap (dibatalkan tulis finansial).

## 4. Paritas (gerbang)
- Tes unit: registri ↔ kunci `AKSI` tiap modul (kunci pengingat ⊂ kunci daftar), arah valid, ambang N satu fungsi.
- Harness nyata baca-saja (kaos + grapgrap): untuk TIAP pengingat, `count`/`amount` == `{kunci}_count`/`{kunci}_value` di `/summary` daftar
  modul DAN == jumlah baris `?aksi={kunci}`. Kontrol merah: ubah ambang satu sisi → MERAH.

## 5. Pembagian berkas
- BACKEND2: services/pengingat.py (baru), services/dashboard_v2.py + routers/dashboard_v2.py, faktur_aksi/proforma_aksi (ambang N),
  po_daftar/UMD (sudah punya sql_aksi).
- BACKEND: tagihan_daftar.py + bills.py (`segera_tempo` menggantikan `jatuh_tempo_minggu_ini`, ambang N), so_aksi (bila perlu fungsi
  jumlah per kunci).
  **Wajib untuk paritas**: `/bills/summary` (bills_service.get_summary) memuat `{kunci}_count`/`{kunci}_value` per AKSI_TAGIHAN
  (faktur, proforma, SO, PO, UM vendor SUDAH memuatnya; tagihan belum terukur). Kontrak fungsi: `jumlah_aksi(conn, tenant, hari_ini, kunci) -> (count, Decimal amount)` per modul.
- FE (WORKSPACE/FRONTEND): baca `reminders` + `sum_in`/`sum_out`; chip "Jatuh tempo minggu ini" → `segera_tempo`.

## 6. Putusan yang dibutuhkan
1. (pemilik, LANGSUNG) migrasi kolom `pengingat_segera_hari` + perubahan perilaku daftar "segera" (3 hari / akhir minggu → N=7 hari).
2. (MASTER) `belum_diterapkan` UM vendor masuk v1?; `ar_due_today` per faktur (aksi WA per faktur) dipertahankan di `tasks` usang atau
   dilebur ke `segera_tempo`?

## 7. PUTUSAN (22 Okt)
- **Pemilik LANGSUNG (sesi BACKEND2)**: SATU definisi "segera jatuh tempo" = jatuh tempo dalam **N hari, bawaan 7**, bisa diatur per usaha;
  kolom `accounting_settings.pengingat_segera_hari` (migrasi **V450**, blok BACKEND2 baru V450–V454 dari MASTER) `INT NOT NULL DEFAULT 7
  CHECK (1..60)`. Pill/chip "segera" faktur, proforma, tagihan berubah 3 hari / akhir minggu → N hari.
- **MASTER**: `vendor_deposits/belum_diterapkan` TIDAK di v1 (tiket). `ar_due_today` per faktur (aksi WA) tetap di `tasks` USANG; registri
  tidak memuat "jatuh tempo hari ini" (tak ada ?aksi yang sama). `/bills/summary` {k}_count/_value = prasyarat paritas (BACKEND).
- **FE (WORKSPACE/FRONTEND)**:
  - `reminders`, `sum_in`, `sum_out` SELALU ada di respons (boleh kosong/"0.00") + `pengingat_versi: 1`.
  - `list` SELALU `{modul, aksi}`, `modul` ∈ ENUM {sales_invoices, proformas, sales_orders, bills, purchase_orders}. FE yang tahu flag daftar
    seragam per tenant -> FE menampilkan kartu TANPA tautan bila daftar modul itu tak aktif (angka & jumlah tetap).
  - `count` int; `amount` string 2dp; `null` HANYA bila arah `non_uang`.
  - Alias `jatuh_tempo_minggu_ini` (faktur chip + tagihan) dipertahankan sampai rilis FE T1 yang memakai `segera_tempo` TERVERIFIKASI.
  - `sum_in` hanya arah masuk; `sum_out` hanya arah keluar (AP tak tercampur).

## 8. Tambahan 23 Okt (MASTER, pola QBO/Xero) — sum_in dipecah, aditif, `pengingat_versi` tetap 1
- `sum_in_ditagih` = Σ amount arah masuk KECUALI `sales_orders/perlu_minta_dp` (piutang + DP yang SUDAH diminta, mis. `menunggu_dp`).
- `sum_in_dp_belum_ditagih` = Σ amount `sales_orders/perlu_minta_dp` (DP yang belum diminta = tugas, bukan uang).
- Invarian: `sum_in == sum_in_ditagih + sum_in_dp_belum_ditagih` (string 2dp). Medan lama tak berubah. Harness nyata: dp == summary SO
  `perlu_minta_dp_value`, ditagih == Σ summary `{k}_value` masuk lain; kontrol merah `merah_dp`.

## 9. Tambahan 23 Okt — bills `segera_tempo` aktif di registri (setelah BILLS-SEGERA 1e19a14f)
- Registri: (bills, segera_tempo, "Tagihan segera jatuh tempo", keluar, week, purchase_invoice); `_tagihan` meneruskan ambang N ke
  `TD.sql_aksi_berparam` (predikat ?aksi= daftar yang sama). `sum_out` = lewat_jatuh_tempo + segera_tempo (partisi: eff < hari ini vs ≥ hari ini).
- Harness nyata: count == summary == ?aksi; `sum_out` == Σ summary keluar; kontrol merah `merah_tagihan` (count +1) WAJIB FAIL.
