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
| REVENUE_SALES_DISCOUNT | 4-10200 | Diskon Penjualan | REVENUE (contra) — Fase D2-wrap D (V157), 5/5 tenants. Flips receive_payments.py:1602 hardcoded fallback (was non-existent literal 6-10100). |
| COGS_SALES | 5-10100 | HPP - Pembelian Barang | COGS |
| COGS_PURCHASE_RETURN | 5-10300 | Retur Pembelian | COGS (contra) |
| VAT_OUTPUT | 2-10600 | PPN Keluaran | LIABILITY — was interim 2-10300 (Fase D1 repoint, V155). `is_interim=false`. 5/5 tenants. |
| VAT_INPUT | 1-10800 | PPN Masukan | ASSET — backfilled 3/5 missing tenants in V154, now 5/5 (V155 mapping). |
| WHT_PPH_PAYABLE | 2-10320 | Hutang PPh Transaksi | LIABILITY — PPh 23/22/4(2) AP-transaction withholding ONLY. BUKAN payroll. V154 seed + V155 mapping, 5/5. |
| WHT_PPH_PREPAID | 1-10820 | PPh Dibayar Dimuka | ASSET — customer potong PPh dari kita = kredit pajak. V154 seed + V155 mapping, 5/5. |
| AP_PREPAID | 1-10550 | Uang Muka Pembelian | ASSET — Fase D2-wrap B (V156), 5/5 tenants. Akun BARU di-seed lewat backfill V156 (existing tenants) + patched `seed_default_coa()` (new tenants). Flips `bill_payments.py:254,381` hardcoded `1-10500` (AR_OTHER = piutang non-trade, semantically wrong for advance to vendor). |
| PURCHASE_DISCOUNT | 5-10200 | Diskon Pembelian | COGS (contra) — Fase D2-wrap B (V156), 5/5 tenants. Akun sudah ada di standar CoA 5 tenant; V156 hanya seed role mapping. Flips `bill_payments.py:40` const `PURCHASE_DISCOUNT_ACCOUNT`. |
| WIP_GENERIC | (pending) | Work-in-Progress (unified) | ASSET — D3.1 catalog promote (V158). **MAPPED PENDING D3.2** (belum di-seed ke account_roles). Single WIP bucket: semua biaya produksi (raw+labor+overhead) unified di sini, tidak dipisah granular. Future split via `WIP_RAW/WIP_LABOR/WIP_OVERHEAD` reservasi. Akan flips literal di `production.py` saat D3.3. |
| COGS_VARIANCE_PRODUCTION | (pending) | HPP Varian Produksi (lumped) | COGS — D3.1 catalog promote (V158). **MAPPED PENDING D3.2**. Varian total material+labor+overhead lumped (tidak granular per cost element). Existing granular `COGS_VARIANCE_MATERIAL/LABOR/OVERHEAD` tetap reserved untuk future per-element variance. |
| WIP_SUBCONTRACT | (pending) | Biaya Subkontrak/Maklon | ASSET — D3.1 catalog promote (V158). **MAPPED PENDING D3.2**. Untuk biaya subcontract/maklon yang masuk ke WIP. |
| INVENTORY_ADJUSTMENT_EXPENSE | (pending) | Biaya Penyesuaian Persediaan | EXPENSE — D3.1 catalog promote (V158). **MAPPED PENDING D3.2**. Generic stock adjustment loss; akan flips literal di `stock_adjustments.py` saat D3.3. |

## TIER 2 — CORRECTED (versi INI yang benar — JANGAN pakai mapping agen inventaris)
| Role | Kode | Nama Akun | Catatan |
|------|------|-----------|---------|
| CASH_PETTY | 1-10300 | Kas Kecil | BUKAN AR. Agen inventaris keliru memetakan AR_TRADE → 1-10300. |
| AR_TRADE | 1-10400 | Piutang Usaha | Semua posting AR (sales_invoices, credit_notes, cheques) ke sini. |

## TIER 3 — PENDING (JANGAN seed sekarang)
| Role | Kode di kode | Masalah | Keputusan |
|------|--------------|---------|-----------|
| ACCUMULATED_DEPRECIATION | 1-20200 (fixed_assets:839) | 1-20200 = Bangunan, bukan akum. penyusutan (1-20900). Kemungkinan bug. | Verifikasi → koreksi ke 1-20900. |
| IC_SALES | (TBD intercompany code) | 4-10200 sekarang DIKLAIM oleh REVENUE_SALES_DISCOUNT (V157, Fase D2-wrap D). Intercompany butuh kode terpisah saat MVP+. | Intercompany DIPARKIR (post-MVP). |
| REVENUE/COGS/EXPENSE fallback | 4-1000 / 5-1000 / 6-1000 (reports COALESCE) | Prefix generik placeholder. | Map ke filter account_type, bukan literal. |
| BRANCH_AR/AP | 1-10950 / 2-10950 | Branches DIPARKIR (post-MVP). | — |
| VAT_INPUT_NONCREDITABLE | (belum dipakai) | — | Tetap di catalog, NOT seeded. |
| VAT_PAYABLE_NET | (belum dipakai) | — | Tetap di catalog, NOT seeded. |
| WHT_PPH21 / WHT_PPH23 / WHT_PPH4_2 / WHT_PPH22 | Granular per-pasal | Q2: reservasi forward-compat di CHECK constraint. Mapping unified pakai WHT_PPH_PAYABLE / WHT_PPH_PREPAID untuk sekarang. | Tetap reservasi, NOT mapped di D1. |

## ROLE TANPA AKUN (gap masa depan)
INVENTORY_RAW/WIP/FINISHED · MFG_OVERHEAD_* · MFG_DIRECT_LABOR · INVENTORY_WRITEOFF_EXPIRED/DAMAGE/SHRINKAGE · AR_ALLOWANCE (CKPN) · AP_ACCRUED.

## RESERVED — D3.1 forward-compat (V158 catalog only, BUKAN TIER 1, NOT seeded)
Role berikut ditambah ke CHECK constraint untuk forward-compat. **JANGAN seed sekarang.**
| Role | Tujuan | Keputusan D3.1 |
|------|--------|----------------|
| WIP_RAW | Granular WIP: raw material portion | Reserved. Pakai `WIP_GENERIC` untuk sekarang (unified bucket). Future split per cost-element. |
| WIP_LABOR | Granular WIP: direct labor portion | Reserved. Pakai `WIP_GENERIC`. |
| WIP_OVERHEAD | Granular WIP: applied overhead portion | Reserved. Pakai `WIP_GENERIC`. |
| FG_FINISHED | Finished Goods (terpisah dari merchandise) | **Keputusan owner D3.1: TIDAK dipisah sekarang.** Semua FG → `INVENTORY_MERCHANDISE`. Reserved untuk future MFG-only tenants. |
| WRITEOFF_DAMAGE | Writeoff rusak (alias singkat) | Reserved forward-compat farmasi/F&B. `INVENTORY_WRITEOFF_DAMAGE` existing tetap di catalog. |
| WRITEOFF_EXPIRED | Writeoff kadaluwarsa (alias singkat) | Reserved forward-compat farmasi/F&B. `INVENTORY_WRITEOFF_EXPIRED` existing tetap. |
| WRITEOFF_SHRINKAGE | Writeoff susut/hilang (alias singkat) | Reserved forward-compat farmasi/F&B. `INVENTORY_WRITEOFF_SHRINKAGE` existing tetap. |

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
  - ~~`receive_payments` — AR_TRADE + CUSTOMER_DEPOSIT_LIABILITY + REVENUE_SALES_DISCOUNT — precondition CLEAN 5/5~~ ✅ **DONE D2.4 + D2-wrap D** (V157 REVENUE_SALES_DISCOUNT seed 5/5 + flip; WHT_PPH_PREPAID = no AR-PPh code path, future feature ticket)
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

## DEFERRED LITERALS (per-modul, post-D1 batch)

Tracking literal CoA codes intentionally retained pending follow-up role
catalog work. Each entry is tracked by file path + line + reason +
target wave. Tests in `backend/tests/account_roles/` assert the literal
list per file (regression guard + deferred-list tracker).

### Tax pollution (URGENT — close at D2 batch)

- ~~bills_service.py line 1537 (PPh fallback `2-10300` → WHT_PPH_PAYABLE `2-10320`)~~ DONE D2.3
- ~~bill_payments.py line 234 (PPh fallback `2-10300` → WHT_PPH_PAYABLE `2-10320`)~~ DONE D2.3
- expenses.py PPh path — DONE D2.1
- vendor_credits.py PPh path — N/A (no PPh emit)
- sales_invoices indirect tax-code path — D2-wrap trace (audit upstream)

### Non-tax deferred (D3 / D2-wrap)

- bills_service.py lines ~2845, ~3340: subcontract ternary `1-10650 / 1-10600`
  → **D3 manufaktur** (role WIP_SUBCONTRACT will be created in that phase;
  promote to AccountRole catalog + V-migration seed before flipping).
- ~~bill_payments.py lines ~254, ~380: vendor deposit `1-10500`~~
  ✅ **DONE D2-wrap B** (V156 promote `AP_PREPAID` role → new account
  `1-10550 Uang Muka Pembelian`, ASSET / DEBIT, seeded 5/5 tenants.
  Naming chosen: **AP_PREPAID** (consistent with WHT_PPH_PREPAID / AR
  family). Literal `1-10500` AR_OTHER was semantically wrong — Piutang
  Lain-lain is receivable non-trade, not advance to vendor. Resolver
  inline at both call sites; precondition gate extended with AP_PREPAID
  + PURCHASE_DISCOUNT.)
- ~~receive_payments.py line ~1602: sales discount fallback `6-10100`~~
  ✅ **DONE D2-wrap D** (V157 seed `REVENUE_SALES_DISCOUNT` → `4-10200`,
  5/5 tenants. Literal removed; resolver inline with handler-level
  precondition gate. Pre-flight discovery: original literal `6-10100` did
  NOT exist in any tenant CoA — flip also fixes latent silent-skip bug on
  unbalanced journal when discount line dropped (Law 4 risk).)
- ~~bill_payments.py line ~40: purchase discount const `5-10200`~~
  ✅ **DONE D2-wrap B** (V156 promote `PURCHASE_DISCOUNT` role → existing
  `5-10200 Diskon Pembelian` (COGS contra, CREDIT), seeded 5/5 tenants.
  Const `PURCHASE_DISCOUNT_ACCOUNT` removed; resolver inline; precondition
  gate extended.)
- Reclassify ticket: grapgrap INVOICE Rp 41,600 misposted to `2-10300`
  between D1 deploy and D2.3 (single tenant, single source_type). Folds
  into D2-wrap micro cleanup (post adjusting journal repointing
  2-10300 → 2-10320). Not a code change.

### Audit window summary (post-D2.3)

Post-D1 V155 deploy, `2-10300` mispost sources:
- BILL / BILL_PAYMENT / PAYMENT_BILL: **zero rows** (D2.3 closes source).
- INVOICE: 1 row (grapgrap, Rp 41,600) — reclassify ticket above.
- Receive payment + sales-invoice indirect tax-code paths: pending D2.4
  audit (WHT_PPH_PREPAID direction; new asset role).
