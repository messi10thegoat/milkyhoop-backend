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

## TIER 2 — CORRECTED (versi INI yang benar — JANGAN pakai mapping agen inventaris)
| Role | Kode | Nama Akun | Catatan |
|------|------|-----------|---------|
| CASH_PETTY | 1-10300 | Kas Kecil | BUKAN AR. Agen inventaris keliru memetakan AR_TRADE → 1-10300. |
| AR_TRADE | 1-10400 | Piutang Usaha | Semua posting AR (sales_invoices, credit_notes, cheques) ke sini. |
| VAT_OUTPUT | 2-10300 (interim) | Utang Pajak (interim PPN Keluaran) | **INTERIM** — berbagi `2-10300` dengan PPh sementara. Fase D pisahkan ke akun PPN Keluaran dedicated. `is_interim=true` di account_roles. |

## TIER 3 — PENDING (JANGAN seed sekarang — menyusul di Fase D)
| Role | Kode di kode | Masalah | Keputusan |
|------|--------------|---------|-----------|
| VAT_INPUT | 1-10800 (expenses.py) | 1-10800 = "PPN Masukan" ada di 2/5 tenant saja (grapgrap, milkytest). Untuk 3 tenant lain expenses berPPN GAGAL (pattern sama dengan VAT_OUTPUT bug C1.1). | Fase D: seed akun PPN Masukan dedicated untuk 5/5 tenant + role mapping. |
| WHT_PPH_PREPAID | (belum ada) | Sisi AR: customer memotong PPh dari kita → kredit pajak (ASET) PPh Dibayar Dimuka. | Fase D: buat akun baru, masuk batch pajak. |
| WHT_PPH_PAYABLE | 2-10300 (nyangkut) | Sisi AP: kita memotong PPh dari vendor → Hutang PPh (LIABILITAS). Saat ini nyangkut di `2-10300` bareng VAT_OUTPUT (tidak bisa split SPT). | Fase D: pisahkan ke akun Hutang PPh dedicated + role mapping. |
| ACCUMULATED_DEPRECIATION | 1-20200 (fixed_assets:839) | 1-20200 = Bangunan, bukan akum. penyusutan (1-20900). Kemungkinan bug. | Verifikasi → koreksi ke 1-20900. |
| IC_SALES | 4-10200 (intercompany) | 4-10200 = Diskon Penjualan, dipakai sbg IC_SALES. | Intercompany DIPARKIR (post-MVP). |
| REVENUE/COGS/EXPENSE fallback | 4-1000 / 5-1000 / 6-1000 (reports COALESCE) | Prefix generik placeholder. | Map ke filter account_type, bukan literal. |
| BRANCH_AR/AP | 1-10950 / 2-10950 | Branches DIPARKIR (post-MVP). | — |

## ROLE TANPA AKUN (gap Fase D — seed baru)
VAT_INPUT (PPN Masukan dedicated) · WHT_PPH_PREPAID · WHT_PPH_PAYABLE (split dari 2-10300) · INVENTORY_RAW/WIP/FINISHED · MFG_OVERHEAD_* · MFG_DIRECT_LABOR · INVENTORY_WRITEOFF_EXPIRED/DAMAGE/SHRINKAGE · AR_ALLOWANCE (CKPN) · AP_ACCRUED.

## DESAIN PAJAK FASE D (WHT — 2 arah berbeda, JANGAN satu role)
- **`WHT_PPH_PREPAID`** (ASET) — sisi AR: customer memotong PPh dari pembayaran ke kita (mis. PPh 23 dipotong customer). Dr di receive_payments.
- **`WHT_PPH_PAYABLE`** (LIABILITAS) — sisi AP: kita memotong PPh dari pembayaran ke vendor (mis. PPh 21/23/4(2)). Cr di bill_payments. Saat ini nyangkut di 2-10300 bareng VAT_OUTPUT.

Fase D ekspansi: `WHT_PPH21_PAYABLE`, `WHT_PPH23_PAYABLE`, `WHT_PPH4_2_PAYABLE`, `WHT_PPH22_PREPAID` (granular per pasal).

## SCOPE MVP (keputusan owner)
- Migrasi Fase C (inti aktif sebelum Fase D): sales_invoices ✅ C1.1, sales_receipts ✅ C1.2, transactions, customer_deposits, opening_balance.
- **DEFERRED BATCH post-Fase D** (terblokir akun pajak baru — dimigrasi koheren setelah seed pajak siap):
  - `bills_service` — VAT_INPUT + AP_TRADE + WHT_PPH_PAYABLE
  - `vendor_credits` — VAT_INPUT + AP_TRADE
  - `receive_payments` — AR_TRADE + WHT_PPH_PREPAID
  - `expenses` (line 1423 `1-10800`) — VAT_INPUT
- DIPARKIR (post-MVP, jangan disentuh): intercompany, branches, cheques. fixed_assets = tiket bug terpisah.

## POLA BANK/KAS (konsisten Fase C)
- BANK_OPERATIONAL = **fallback-only**. Posting bank wajib pakai `bank_account_id` user-picked.
- Kalau user_picked NULL DAN role fallback unmapped → **raise 422** (jangan silent skip Dr/Cr line — Law 4 violation).
- JANGAN force-seed BANK_OPERATIONAL ke tenant ambiguous (multiple bank leafs). Fallback+422 cukup.

## TIKET TERPISAH (jangan dicampur ke CoA)
- Rule 8 grapgrap AR drift −28,25 jt → cek V139 sudah apply di DB live atau belum.
- DEP-2604-0001 credit ke 2-10400 Utang Gaji (bukan deposit liability) → bug posting customer deposit.
