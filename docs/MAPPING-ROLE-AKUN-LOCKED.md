# MAPPING ROLE ↔ AKUN — SUMBER KEBENARAN (LOCKED)

Otoritatif = DB live + forensik Fase A, BUKAN skill ARAP (outdated).
Status: CONFIRMED = aman dikunci · CORRECTED = pernah salah, versi ini yang benar · PENDING = jangan dikunci.

## TIER 1 — CONFIRMED (seed sekarang)
| Role | Kode | Nama Akun | Type |
|------|------|-----------|------|
| CASH_GENERAL | 1-10100 | Kas | ASSET |
| BANK_OPERATIONAL | 1-10200 | Bank | ASSET |
| AR_TRADE | 1-10400 | Piutang Usaha | RECEIVABLE |
| AR_OTHER | 1-10500 | Piutang Lain-lain | ASSET |
| INVENTORY_MERCHANDISE | 1-10600 | Persediaan Barang Dagangan | ASSET |
| AP_TRADE | 2-10100 | Hutang Usaha | PAYABLE |
| CUSTOMER_DEPOSIT_LIABILITY | 2-10500 | Uang Muka Pelanggan | LIABILITY |
| REVENUE_DEFERRED | 2-10750 | Pendapatan Diterima Dimuka | LIABILITY (contract liability) — inti model 3-event PSAK 72 (V137). Billing event credit; revenue event debit. Promoted ke TIER 1 di Fase C1.1 addendum (V151+V152). |
| EQUITY_OPENING_BALANCE | 3-50000 | Modal Saldo Awal | EQUITY |
| REVENUE_SALES_GOODS | 4-10100 | Penjualan | REVENUE |
| REVENUE_SALES_RETURN | 4-10300 | Retur Penjualan | REVENUE (contra) |
| COGS_SALES | 5-10100 | HPP - Pembelian Barang | COGS |
| COGS_PURCHASE_RETURN | 5-10300 | Retur Pembelian | COGS (contra) |
| VAT_OUTPUT | 2-10600 | PPN Keluaran | LIABILITY — was interim 2-10300 (Fase D1 repoint, V155). `is_interim=false`. 5/5 tenants. |
| VAT_INPUT | 1-10800 | PPN Masukan | ASSET — backfilled 3/5 missing tenants in V154, now 5/5 (V155 mapping). |
| WHT_PPH_PAYABLE | 2-10320 | Hutang PPh Transaksi | LIABILITY — PPh 23/22/4(2) AP-transaction withholding ONLY. BUKAN payroll. V154 seed + V155 mapping, 5/5. |
| WHT_PPH_PREPAID | 1-10820 | PPh Dibayar Dimuka | ASSET — customer potong PPh dari kita = kredit pajak. V154 seed + V155 mapping, 5/5. |

## TIER 2 — CORRECTED (versi INI yang benar — JANGAN pakai mapping agen inventaris)
| Role | Kode | Nama Akun | Catatan |
|------|------|-----------|---------|
| CASH_PETTY | 1-10300 | Kas Kecil | BUKAN AR. Agen inventaris keliru memetakan AR_TRADE → 1-10300. |
| AR_TRADE | 1-10400 | Piutang Usaha | Semua posting AR (sales_invoices, credit_notes, cheques) ke sini. |

## TIER 3 — PENDING (JANGAN seed sekarang)
| Role | Kode di kode | Masalah | Keputusan |
|------|--------------|---------|-----------|
| ACCUMULATED_DEPRECIATION | 1-20200 (fixed_assets:839) | 1-20200 = Bangunan, bukan akum. penyusutan (1-20900). Kemungkinan bug. | Verifikasi → koreksi ke 1-20900. |
| IC_SALES | 4-10200 (intercompany) | 4-10200 = Diskon Penjualan, dipakai sbg IC_SALES. | Intercompany DIPARKIR (post-MVP). |
| REVENUE/COGS/EXPENSE fallback | 4-1000 / 5-1000 / 6-1000 (reports COALESCE) | Prefix generik placeholder. | Map ke filter account_type, bukan literal. |
| BRANCH_AR/AP | 1-10950 / 2-10950 | Branches DIPARKIR (post-MVP). | — |
| VAT_INPUT_NONCREDITABLE | (belum dipakai) | — | Tetap di catalog, NOT seeded. |
| VAT_PAYABLE_NET | (belum dipakai) | — | Tetap di catalog, NOT seeded. |
| WHT_PPH21 / WHT_PPH23 / WHT_PPH4_2 / WHT_PPH22 | Granular per-pasal | Q2: reservasi forward-compat di CHECK constraint. Mapping unified pakai WHT_PPH_PAYABLE / WHT_PPH_PREPAID untuk sekarang. | Tetap reservasi, NOT mapped di D1. |

## ROLE TANPA AKUN (gap masa depan)
INVENTORY_RAW/WIP/FINISHED · MFG_OVERHEAD_* · MFG_DIRECT_LABOR · INVENTORY_WRITEOFF_EXPIRED/DAMAGE/SHRINKAGE · AR_ALLOWANCE (CKPN) · AP_ACCRUED.

## DESAIN PAJAK FASE D1 (LOCKED — V154 + V155)
- **`VAT_OUTPUT`** (LIABILITY) → 2-10600 PPN Keluaran. Repointed dari interim 2-10300.
- **`VAT_INPUT`** (ASSET) → 1-10800 PPN Masukan. Backfilled 3 tenant missing.
- **`WHT_PPH_PAYABLE`** (LIABILITAS) → 2-10320 Hutang PPh Transaksi. **Sisi AP**: kita memotong PPh dari pembayaran ke vendor (mis. PPh 23/22/4(2)). Cr di bill_payments. PAYROLL EXCLUDED.
- **`WHT_PPH_PREPAID`** (ASET) → 1-10820 PPh Dibayar Dimuka. **Sisi AR**: customer memotong PPh dari pembayaran ke kita (mis. PPh 23 dipotong customer). Dr di receive_payments.

Granular per-pasal reservasi (Q2): `WHT_PPH21`, `WHT_PPH23`, `WHT_PPH4_2`, `WHT_PPH22` retained in CHECK constraint untuk forward-compat. NOT mapped di D1 — semua arah AP mapping via unified `WHT_PPH_PAYABLE`, semua arah AR via unified `WHT_PPH_PREPAID`. Future: bisa expand ke per-pasal mapping tanpa migration CHECK constraint.

## PKP TOGGLE (per-tenant)
- `Tenant.is_pkp` BOOLEAN NOT NULL DEFAULT true (V154).
- Posting paths pakai `resolve_account_id_by_role_if_pkp(conn, tenant_id, role)` untuk VAT_OUTPUT/VAT_INPUT/VAT_INPUT_NONCREDITABLE/VAT_PAYABLE_NET.
- Non-PKP tenant: VAT roles return None → posting MUST skip emit VAT line (sales/purchase tanpa PPN).
- WHT_PPH roles tidak dipengaruhi toggle (selalu resolve apa adanya).
- Default true untuk backward compat — existing tenants tetap PKP unless explicitly toggled.

## PPH 21 PAYROLL BOUNDARY
- `2-10310 Utang PPh 21` = payroll-exclusive, JANGAN pernah resolve via WHT_PPH_PAYABLE.
- `WHT_PPH_PAYABLE` (2-10320) untuk PPh 23/22/4(2) AP-transaction withholding ONLY.
- Payroll module retains own account + role mapping (out of D1 scope).
- Regression guard ada di `test_fase_d1_tax_split.py::test_wht_pph_payable_never_points_to_10310`.

## SCOPE MVP (keputusan owner)
- Migrasi Fase C (inti aktif sebelum Fase D): sales_invoices ✅ C1.1, sales_receipts ✅ C1.2, transactions, customer_deposits, opening_balance.
- **DEFERRED BATCH — READY untuk migrasi (post-D1)**:
  - `bills_service` — AP_TRADE + VAT_INPUT + WHT_PPH_PAYABLE + INVENTORY_MERCHANDISE — precondition CLEAN 5/5 ✅
  - `vendor_credits` — AP_TRADE + VAT_INPUT + COGS_PURCHASE_RETURN — precondition CLEAN 5/5 ✅
  - `receive_payments` — AR_TRADE + CASH_GENERAL + WHT_PPH_PREPAID — precondition CLEAN 5/5 ✅
  - `expenses` — VAT_INPUT + CASH_GENERAL + AP_TRADE — precondition CLEAN 5/5 ✅
- DIPARKIR (post-MVP, jangan disentuh): intercompany, branches, cheques. fixed_assets = tiket bug terpisah.

## POLA BANK/KAS (konsisten Fase C)
- BANK_OPERATIONAL = **fallback-only**. Posting bank wajib pakai `bank_account_id` user-picked.
- Kalau user_picked NULL DAN role fallback unmapped → **raise 422** (jangan silent skip Dr/Cr line — Law 4 violation).
- JANGAN force-seed BANK_OPERATIONAL ke tenant ambiguous (multiple bank leafs). Fallback+422 cukup.

## TIKET TERPISAH (jangan dicampur ke CoA)
- Rule 8 grapgrap AR drift −28,25 jt → cek V139 sudah apply di DB live atau belum.
- DEP-2604-0001 credit ke 2-10400 Utang Gaji (bukan deposit liability) → bug posting customer deposit.
- Saldo 2-10300 grapgrap Rp 56.600 cr historis — immaterial, repoint go-forward (D1), no adjusting entry. Fold cleanup ke D2 jika perlu.
- V011 long-term: keep deprecated marker; eventually delete or relocate ke `migrations/archive/`.
- Payroll integration verify: pastikan payroll posting tidak pernah resolve WHT_PPH_PAYABLE — confirm direct `2-10310` resolution only.
