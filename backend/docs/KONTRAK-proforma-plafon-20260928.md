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
