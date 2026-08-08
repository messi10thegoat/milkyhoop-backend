# TEMUAN ARSITEKTURAL: 94 jalur DRAFT→POSTED di 32 file, nol kernel posting

**Tanggal:** 2026-08-06 **Kelas:** arsitektural (bukan bug tunggal)
**Kelas bukti:** `[CODE]` terukur

## Fakta

```
grep -rn "SET status = 'POSTED'" backend/ --include=*.py
  → 94 jalur
  → 32 file
  → NOL fungsi kernel yang dilewati semua jalur
```

`journals.py:527 post_journal` hanya endpoint jurnal manual, bukan kernel bersama.
`backend/services/accounting_kernel/` berisi config/models/validators/reports — **bukan** gerbang posting.

File yang memuat jalur posting: `sales_invoices.py`, `production.py`, `customer_deposits.py`,
`vendor_deposits.py`, `bills_service.py`, `sales_receipts.py`, `opening_balance.py`,
`receive_payments.py`, `stock_adjustments.py`, `journals.py`, `payroll.py`, `payroll_payments.py`,
`bank_transfers.py`, `intercompany.py`, `customers.py`, `vendors.py`, `cheques.py`, `periods.py`,
`bill_payments.py`, `inventory_helpers.py`, `kernel_document_executor.py`,
`payment_request_service.py`, dll (32 total).

## Kenapa ini melampaui bug biaya bank

Bug biaya bank hanyalah **gejala pertama yang kebetulan terdeteksi**. Konsekuensinya berlaku untuk
**setiap invariant posting**, sekarang dan yang akan datang:

| Invariant | Ditegakkan di mana hari ini | Risiko |
|---|---|---|
| Saldo baris == header (Law 4) | 1 dari 94 jalur (`bill_payments.py`) | 93 jalur bisa memposting timpang |
| Period lock (Law 5) | DB trigger `trg_prevent_closed_period_journal` ✅ | aman — **karena di DB** |
| Hash chain (Law 20/22) | DB trigger `trg_assign_hash_sequence` ✅ | aman — **karena di DB** |
| Advisory lock (Law 13) | per-jalur, konvensi kode | jalur baru bisa lupa |
| Idempotency (Law 14) | per-jalur, konvensi kode | terbukti bolong (audit terpisah) |
| Permission / audit trail | middleware + per-jalur | belum diaudit |

**Pola yang terlihat:** invariant yang ditegakkan **di DB** selamat; yang ditegakkan **oleh konvensi
kode** bocor. Itu bukan kebetulan — 94 tempat berarti 94 kesempatan lupa, dan jalur ke-95 (fitur
berikutnya) tidak punya siapa pun yang mengingatkan.

## Konsekuensi praktis

1. **"Naikkan guard ke kernel" bukan opsi** — kernelnya tidak ada. Setiap usulan yang berbunyi
   "taruh saja di satu tempat" harus lebih dulu menjawab: tempat yang mana?
2. **DB adalah satu-satunya chokepoint nyata** yang dilewati 94 jalur tanpa terkecuali — termasuk
   skrip, migrasi, dan mutasi manual yang tak lewat gateway sama sekali.
3. **Menyalin guard ke 94 tempat = konvensi yang pasti bocor.** Biaya perawatannya juga 94×.

## Dua arah penyelesaian (tidak eksklusif)

**A. Jangka pendek — pindahkan invariant ke DB.** Setiap Law yang bisa dinyatakan sebagai predikat
atas baris DB sebaiknya jadi trigger/constraint. Usulan pertama:
`DOCS/proposals/2026-08-06-trigger-line-header-balance.md`.

**B. Jangka panjang — bangun kernel posting sungguhan.** Satu fungsi
`post_journal(conn, tenant, header, lines, source_type, ...)` yang: ambil advisory lock → validasi
saldo → cek idempotency → INSERT DRAFT → INSERT lines → UPDATE POSTED. Semua jalur memanggilnya.
Refactor besar (94 titik panggil) — **jangan** dikerjakan sekaligus; migrasi bertahap per modul,
dengan trigger DB (A) sebagai jaring pengaman selama transisi.

**Rekomendasi: kerjakan A sekarang, B sebagai arah.** A murah dan menutup kelas; B menghapus akar
tapi butuh berbulan-bulan dan berisiko tinggi kalau dipaksakan cepat.

## Catatan window
Blast radius saat ini nol tenant nyata (pindaian jurnal timpang: `milkydb` 2/13 — keduanya artefak
bug biaya bank, net 0; semua DB arsip 0). Ini window termurah untuk memasang penegak di DB.
