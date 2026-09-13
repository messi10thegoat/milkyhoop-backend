# TIKET — `verify_ar_reconciliation_all()` buta terhadap piutang tak teratribusi (13 Sep 2026)

**Status:** TERBUKA. Tidak diperbaiki. **Putusan pemilik belum diambil** — putusan "betulkan + patok"
untuk check_3 (R9) **tidak otomatis berlaku** di sini.

**Rujukan silang wajib:** `TIKET-pembalikan-generik-memutus-ikatan-20260913.md`.

## Mekanisme (dibaca)

`verify_ar_reconciliation(p_tenant)` mengatribusi GL RECEIVABLE ke pelanggan **hanya** untuk
`source_type` INVOICE, RECEIVE_PAYMENT/PAYMENT_RECEIVED, DEPOSIT_APPLICATION, CREDIT_NOTE (lewat
`original_invoice_id`), INVOICE_REVERSAL. Selain itu `customer_id` NULL → `WHERE m.customer_id IS NOT
NULL` **membuang barisnya**. Penjaga yang menyaring keluar cacatnya.

## Bukti (eksekusi, ROLLBACK, grapgrap, nol jurnal uji menetap)

| langkah | GL efektif sebenarnya | guard total_gl | verdict |
|---|---|---|---|
| awal | −180.000 | 20.000 | PASS (selisih 200.000 = CN-2608-0001 tanpa faktur asal, tak terlihat) |
| **UJI** jurnal MANUAL POSTED kredit piutang 777 | −180.777 | **20.000 (diam)** | **PASS — BUTA** |
| kontrol INVOICE ke faktur nyata +777 | −179.223 | 20.777 | PASS — **bukan pembeda** (GL & compute naik bersama) |
| **KONTROL PEMBEDA** RECEIVE_PAYMENT bersumber penerimaan nyata −777 | −180.777 | 19.223, drift −777 | **FAIL_NON_EXEMPT** — penjaga BISA merah |

## Kenapa bobotnya naik — dua cacat saling menutupi

- Uji di atas memasukkan jurnal MANUAL lewat **SQL langsung**: itu **hanya bukti kebutaan penjaga**.
  **Aplikasi TIDAK bisa membuat MANUAL ke piutang lewat layar pembuatan jurnal** — pintu itu berpagar
  (`validate_no_derived_layer_accounts()`, `journals.py` ~483).
- **Jalur aplikasi yang nyata bocor adalah PINTU PEMBALIKAN** (`journals.py` ~698, tanpa validator
  itu): 65 jurnal pembalikan MANUAL di kaos-biru.
- Penjaga rekonsiliasi **buta tepat pada jenis jurnal yang lolos lewat pintu yang tak berpagar** →
  kita tak pernah bisa tahu apakah piutang bergeser lewat jalur itu. Siapa pun yang memperbaiki salah
  satu tiket harus tahu yang lain masih terbuka.


---

## RESOLUTION — V248 live (14 Sep 2026)

**Owner decisions (14 Sep):**
- (a) CN-2609-0006 (kaos-biru 25,000) NOT pinned: the red stays visible and clears once it is applied through unit B.
- (b) CN-2608-0001 (grapgrap 200,000) PINNED by fingerprint, decided_by=owner.
- (c) L3 per invoice now.

**Design (no code shared with compute_ar_outstanding / is_effective_journal):**
- `ar_klaim_piutang(tenant)`: every POSTED RECEIVABLE line is claimed to an invoice. Claim rules:
  - INVOICE;
  - CN via original_invoice_id;
  - deposit applied (exactly 1 application per journal);
  - receipt proportional to its allocations;
  - a reversal mirrors its original's claim line by line (only when the pair cancels exactly).
- `verify_ar_reconciliation_rincian(tenant)`:
  - L2 residual (line, unclaimed remainder) vs fingerprint pins → RESIDU_TERPATOK / RESIDU_TAK_TERPATOK / PIN_BASI;
  - L3 claim per invoice vs compute per invoice.
- `verify_ar_reconciliation_all()`: **cron column contract unchanged** (check_14 reads verdict + total_drift). PASS / PASS_EXEMPT (residual == pins); FAIL_STALE_PIN / FAIL_UNPINNED / FAIL_PER_INVOICE / FAIL_TOTAL. It never RAISEs on red data.
- `ar_reconciliation_pins`: pins by line_id + journal_id + account + net + journal content_hash + source. The scalar table `ar_reconciliation_exemptions` is retired (not read; it was empty).

**Prediction BEFORE running → result (dry run in ROLLBACK, then live):**
- residual exactly 2 members, L3 zero → **correct**:
  - grapgrap `4e15a29a` −200,000 (pinned) → PASS_EXEMPT;
  - kaos-biru `ce216061` −25,000 → FAIL_UNPINNED.
- All 8 reversal pairs were claimed (7 INVOICE, 1 RECEIVE_PAYMENT), not residual.

**Gate `scripts/gerbang_v248.py`** (one transaction, ROLLBACK, residue 0; count derived from structure 14):
- `baru` 14/14:
  - baseline;
  - cron contract;
  - S1 MANUAL −777 → residual exactly {−777};
  - S2 offset ±777 (L1 unchanged) → 2 members;
  - S3 unequal reversal → {−(X−1)};
  - control CN posted → 1 member, after apply through handler B → back to the baseline set (no false red);
  - pin +1 rupiah → FAIL_STALE_PIN;
  - independence with a positive control.
- `lama` (live checker before V248): S1/S2/S3 detection all **RED 3/3** = blindness proven.
- `sabotase_cn` (CN claim branch dropped) → the apply control goes RED (also L3). `sabotase_skalar` (L2 read as a scalar) → S2 offset goes RED.
- `hidup` (after install) 14/14, residue 0.
- Emulation of cron check_14 using **the script's own functions** (psql_cmd + check_14): grapgrap CHK_PASS=1; kaos-biru CHK_PASS=0 `AR reconciliation FAIL_UNPINNED (drift=-25000.00)`; no `__GAGAL__` → **not BROKEN**.

**Consequence:** from the 06:00 UTC run onward, the health check reports check_14 kaos-biru as a FAILURE every day until CN-2609-0006 is applied (owner decision a). A TRUE red that used to be hidden.

**Limits:**
- L2 claims a deposit application only when there is exactly 1 application per journal. A journal with >1 application is left unclaimed on purpose (noisy red, not silent).
- L3 compares per invoice; an invoice absent from compute = 0.
- `verify_ar_reconciliation(text)` per customer is NOT deleted (commented as SUPERSEDED).
