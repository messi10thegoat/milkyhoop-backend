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
