# Overpayment uang muka: kosakata metode (L2) + telusur jurnal (L3) — LIVE

**Commit:** `53d360eb` (deploy/master) · **Tanggal:** 2026-09-14 · **Gateway:** healthz 200

## Konteks
Kelebihan bayar piutang (receive_payment `unapplied_amount > 0`) auto-membuat `customer_deposits`
(uang muka). Ukur: **0 overpayment historis sukses** — karena jalur bank_transfer GAGAL post (lihat L2).
FE mengizinkan overpay (tak ada guard total≤outstanding) → jalur TERJANGKAU.

## L2 — kosakata metode (bug fungsional)
`receive_payments.payment_method` ∈ {cash, bank_transfer}; `customer_deposits` ∈ {cash, transfer, check,
other}. Overpayment memasukkan metode APA ADANYA → **bank_transfer melanggar CHECK customer_deposits →
`_post_payment` ROLLBACK → pembayaran GAGAL dipost** (hanya cash lolos). Fix: helper batas
`_deposit_payment_method` (bank_transfer→transfer, cash→cash, lain→400 terbaca, bukan 500 CHECK).

## L3 — telusur jurnal
`created_deposit.journal_id` DULU NULL → deposit tak tertaut ke jurnalnya. Kini = `journal_id` RP (jurnal
yg memuat baris Cr Uang Muka). Saldo deposit tetap journal-derived (tak baca journal_id), jadi ini
telusur/audit, bukan koreksi saldo.

## L4 — non-isu (dikoreksi)
Klaim awal "account_id = bank_accounts.id (salah)" DIRETRAKSI setelah ukur tuntas: baris Dr Kas/Bank RP
memakai `bank_account_id` sebagai account_id jurnal yang **ber-FK ke chart_of_accounts** dan INSERT-nya
jalan SEBELUM blok overpayment → `bank_account_id` DIJAMIN CoA sah saat deposit dibuat. Refund mirror
gate pada `body.bank_account_id` (request), bukan kolom deposit → kolom NULL tak merusak. Skip (per MASTER).

## Gerbang E2E dua-sisi (kontainer, savepoint ROLLBACK, _post_payment + refund NYATA, kaos-biru) — 9/9 GREEN
overpay bank_transfer 150000 (alloc 100000, unapplied 50000) → deposit method='transfer',
journal_id=jurnal RP, account_id=CoA, amount 50000; jurnal RP seimbang 150000/150000; cermin bank RP=1;
refund 50000 (bank_account_id) → cermin refund=1; **hc_bank_sync_members & V248 rincian L3 SEBELUM==SESUDAH**
(journal_id tak menimbulkan hitung-ganda/drift); LAMA 'bank_transfer'→customer_deposits → CHECK violation (RED).
