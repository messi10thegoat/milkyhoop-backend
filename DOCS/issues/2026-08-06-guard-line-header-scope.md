# P0: guard saldo baris-vs-header baru menutup 1 dari 94 jalur posting

**Tanggal:** 2026-08-06 **Severity:** P0 (celah level SCHEMA, bukan level fungsi)
**Status:** OPEN — fix `7713b6fa` menutup gejalanya di satu jalur, **kelasnya belum tertutup**
**Kelas bukti:** `[SQL]` + `[CODE]`, terukur

## Celahnya ada di SCHEMA, bukan di satu fungsi

```sql
-- constraint yang ADA di journal_entries:
je_balanced CHECK ((status = 'DRAFT') OR (abs((total_debit - total_credit)) < 0.01))
```

Ini **hanya membandingkan header dengan header**. Tidak ada apa pun yang memaksa
`SUM(journal_lines.debit) = journal_entries.total_debit`. Akibatnya **setiap** jalur posting bisa
menghasilkan baris timpang di bawah header yang seimbang — persis yang terjadi pada bug biaya bank
(`BP-2608-0001`: header 3.502.500/3.502.500, baris 3.500.000/3.502.500).

## Cakupan guard saat ini — 1 dari 94

`[CODE]` `grep -rn "SET status = 'POSTED'" backend/ --include=*.py`:

- **94 jalur** DRAFT→POSTED
- tersebar di **32 file**
- **NOL kernel posting terpusat** — tidak ada satu fungsi yang dilewati semua `source_type`
  (`journals.py:527 post_journal` hanya endpoint manual journal, bukan kernel bersama)
- Guard `SUM(baris)==header` dari `7713b6fa` ada di **`bill_payments.py` saja**

File dengan jalur posting (jumlah baris `SET status='POSTED'`): `sales_invoices.py`, `production.py`,
`customer_deposits.py`, `vendor_deposits.py`, `bills_service.py`, `sales_receipts.py`,
`opening_balance.py`, `receive_payments.py`, `stock_adjustments.py`, `journals.py`, `payroll.py`,
`bank_transfers.py`, `intercompany.py`, `payroll_payments.py`, `customers.py`, `vendors.py`,
`cheques.py`, `periods.py`, `inventory_helpers.py`, `kernel_document_executor.py`,
`payment_request_service.py`, `bill_payments.py`, dll.

**Menaikkan guard "ke kernel" TIDAK MUNGKIN tanpa refactor besar** — kernelnya tidak ada.
Menyalin guard ke 94 tempat = konvensi yang pasti bocor pada jalur ke-95.

## Pindaian jurnal existing — blast radius NOL di luar tenant test

`[SQL]` seluruh database di host:

| Database | POSTED | Timpang |
|---|---|---|
| `milkydb` (live, tenant test) | 13 | **2** — `BP-2608-0001` + void-nya, net 0, sudah dikenal |
| `milkydb_saved_20260725` | 36 | **0** |
| `milkydb_goldenpath_green_20260725` | 17 | **0** |
| `milkydb_prev_20260724` | 0 | 0 |
| `milkydb_fresh` | 0 | 0 |

**Bug ini tidak pernah merusak buku tenant nyata.** Konsisten: arsip-arsip itu tak pernah
mengeksekusi field biaya bank (blind spot harness — lihat tiket e2e index).

Window ini termurah untuk menutup: satu tenant test, nol pengguna nyata.

## USULAN (belum diimplement) → `DOCS/proposals/2026-08-06-trigger-line-header-balance.md`

DB trigger `BEFORE UPDATE ... WHEN status→POSTED` yang menolak bila
`SUM(lines.debit) <> total_debit` atau `SUM(lines.credit) <> total_credit`.

Argumennya sama dengan UNIQUE index di V218: **konvensi kode bisa dilanggar, trigger tidak.**
Satu trigger menutup 94 jalur sekaligus, termasuk jalur ke-95 yang belum ditulis.

## Uji-bicara wajib (Law 33)

Setiap tempat guard baru dipasang harus dibuktikan bisa MERAH, bukan hanya hijau. Untuk trigger:
INSERT jurnal DRAFT dengan baris sengaja timpang → UPDATE ke POSTED → **harus** ditolak; lalu
perbaiki barisnya → UPDATE **harus** lolos.
