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

## TIER 3 — PENDING (JANGAN seed di Fase B — menyusul di Fase D)
| Role | Kode di kode | Masalah | Keputusan |
|------|--------------|---------|-----------|
| VAT_INPUT | 1-10700 / 1-10800 | 1-10700 = Biaya Dibayar Dimuka (prepaid), bukan PPN Masukan. Tak ada akun PPN Masukan dedicated. | Fase D: buat akun PPN Masukan baru. |
| VAT_OUTPUT vs WHT_PPH | 2-10300 (dipakai dua-duanya) | "Hutang Pajak" generik dipakai PPN Keluaran DAN PPh potong. Tak bisa split SPT. | Fase D: pisah. Interim B: VAT_OUTPUT → 2-10300 (tandai interim). |
| ACCUMULATED_DEPRECIATION | 1-20200 (fixed_assets:839) | 1-20200 = Bangunan, bukan akum. penyusutan (1-20900). Kemungkinan bug. | Verifikasi → koreksi ke 1-20900. |
| IC_SALES | 4-10200 (intercompany) | 4-10200 = Diskon Penjualan, dipakai sbg IC_SALES. | Intercompany DIPARKIR (post-MVP). |
| REVENUE/COGS/EXPENSE fallback | 4-1000 / 5-1000 / 6-1000 (reports COALESCE) | Prefix generik placeholder. | Map ke filter account_type, bukan literal. |
| BRANCH_AR/AP | 1-10950 / 2-10950 | Branches DIPARKIR (post-MVP). | — |

## ROLE TANPA AKUN (gap Fase D — seed baru)
VAT_INPUT (PPN Masukan dedicated) · INVENTORY_RAW/WIP/FINISHED · MFG_OVERHEAD_* · MFG_DIRECT_LABOR · INVENTORY_WRITEOFF_EXPIRED/DAMAGE/SHRINKAGE · AR_ALLOWANCE (CKPN) · AP_ACCRUED · WHT_PPH21/23/4(2)/22 (split dari 2-10300).

## SCOPE MVP (keputusan owner)
- Migrasi Fase C (inti aktif): sales_invoices, bills_service, credit_notes, vendor_credits, sales_receipts, bill_payments, transactions, expenses, opening_balance, customer_deposits.
- DIPARKIR (post-MVP, jangan disentuh): intercompany, branches, cheques. fixed_assets = tiket bug terpisah.

## TIKET TERPISAH (jangan dicampur ke CoA)
- Rule 8 grapgrap AR drift −28,25 jt → cek V139 sudah apply di DB live atau belum.
- DEP-2604-0001 credit ke 2-10400 Utang Gaji (bukan deposit liability) → bug posting customer deposit.
