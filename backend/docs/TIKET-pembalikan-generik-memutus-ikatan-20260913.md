# TIKET — pembalikan jurnal GENERIK memutus ikatan tanpa menyentuh dokumennya

**Status:** TERBUKA, **melingkupi saja**. Nol perbaikan, nol sentuhan data.
**Menyentuh Law 31 Gate 5 (reversal cascade) dan Law 29/31 Gate 4.**

## Bentuknya

`POST` pembalikan jurnal di `journals.py` (INSERT di baris ~698) membuat jurnal
`source_type=MANUAL` ber-`reversal_of_id`. Ia memeriksa **empat** hal:
jurnal ada, bukan DRAFT, belum pernah dibalik, periode terbuka.

**Yang TIDAK diperiksanya:**

1. **`validate_no_derived_layer_accounts()` TIDAK dipanggil.** Validator itu ada
   dan benar — tapi dipasang di jalur **PEMBUATAN** (dipanggil baris 483), bukan
   di jalur **PEMBALIKAN**. Pagarnya di satu pintu, bukan dua.
   Ia lahir 26 Mei (`e39eaeab`), jadi ini bukan "pagar belum ada".
2. **`source_type` sasaran tidak dibatasi.** Jurnal berpunya-dokumen (BILL,
   INVOICE) dan berpunya-layer (PRODUCTION_*) bisa dibalik lewat pintu generik
   ini, sehingga **dokumen sumber dan layer turunannya tak ikut dibalik**.

## Terukur (`kaos-biru-konveksi`, semuanya 3 Sep 2026)

**65 jurnal MANUAL, SEMUANYA pembalikan** (65/65 ber-`reversal_of_id`; nol
jurnal manual biasa). Yang mereka balik:

| source_type yang dibalik | jml | nilai |
|---|---|---|
| PRODUCTION_OVERHEAD | 24 | 5.620.000 |
| PRODUCTION_LABOR | 23 | 5.700.000 |
| **BILL** | **9** | **6.680.000** |
| PRODUCTION_OUTPUT | 5 | 5.459.968 |
| PRODUCTION_VARIANCE | 4 | 800.000 |

~Rp 24 juta lintas **lima** source_type, nol cascade ke layer turunan.

**Akibat yang sudah terlihat:** sembilan tagihan ber-jurnal-terbalik tetap
berbunyi `posted`/`paid` dengan `accounting_status=POSTED` — termasuk
`BILL-2609-0002` yang berbunyi **`paid`**. Jurnalnya terbalik, dokumennya tidak.

⚠️ **`void_bill` sendiri BENAR** — ia menulis `status=void`, `status_v2`,
`operational_status=VOID`, `accounting_status=REVERSED`. Jadi ini **bukan**
cacat jalur void. Cacatnya: **ada jalan lain memutus ikatan yang tak melewatinya.**

**Dugaan yang belum diukur:** pembalikan `PRODUCTION_OUTPUT` kemungkinan
meninggalkan `inventory_ledger` tak terbalik dengan cara yang sama — dan itu
mungkin asal 5 baris `PRODUCTION_OUTPUT` tanpa `journal_id` yang ditemukan saat
melingkupi drift WAC. **BELUM diukur; jangan dikutip sebagai temuan.**

## `check_13` MENGHADAP ARAH YANG SALAH

`check_13_status_desync` mencari *"bills with journal but `accounting_status !=
POSTED`"*. Kelas di atas adalah **kebalikannya**: `accounting_status=POSTED`
sementara jurnalnya sudah dibalik.

**Jadi memperbaiki SQL `check_13` TIDAK menutup kelas ini.** Siapa pun yang
membetulkan sintaksnya lalu mengira desync sudah terjaga akan keliru: penjaga
itu akan hijau dan tetap buta terhadap arah ini. Kelas ini tak terjaga **dua**
kali — sekali karena check_13 tak pernah berjalan (rusak sejak 2026-04-20),
sekali karena arahnya terbalik.

## Yang TIDAK dikerjakan

- **Sembilan dokumen itu TIDAK disentuh.** Merapikannya = menyunting pembukuan
  (Law 2/3) = putusan pemilik, dengan rencananya sendiri.
- Endpoint pembalikan TIDAK diubah. Ia menyentuh Law 31 dan menuntut 7/7 gate
  serta pembacaan companion skill lebih dulu.

---

# TAMBAHAN 13 Sep 2026 — dua pemeriksaan harian kini melihatnya

- **check_13 (arah baru)** menemukan persis 9 tagihan `accounting_status=POSTED`
  yang jurnal BILL-nya sudah dibalik tanpa jurnal hidup: BILL-2609-0002,
  0028..0035, **Rp 6.680.000**. Himpunan yang sama dengan jalur lain.
- **check_7 menemukan TEMUAN AKUNTANSI NYATA:** BILL-2609-0002 (`status_v2=void`)
  — jurnal BILL-nya dibalik, tapi **jurnal pembayaran 100.000-nya masih efektif**.
  GL PAYABLE memuat debit pembayaran atas tagihan yang tak lagi ada di sub-ledger.
  Tagihan batal, pembayarannya tidak.
- Keduanya **dipatok** (V242) atas putusan pemilik membiarkan 9 tagihan itu —
  **bukan karena dianggap wajar**. Anggota ke-10 atau penggantian → merah.

---

# RUJUKAN SILANG 13 Sep 2026

`verify_ar_reconciliation_all()` **terbukti buta** terhadap jurnal piutang ber-`source_type` di luar
lima yang diatribusi (termasuk MANUAL/REVERSAL) — `TIKET-verify-ar-reconciliation-buta-20260913.md`.
Pintu pembalikan tak berpagar (tiket ini) + penjaga yang buta pada jenis jurnal itu = **dua cacat saling
menutupi**. Memperbaiki salah satu tanpa yang lain tetap meninggalkan jalur piutang tak terawasi.


---

## RESOLVED — Gate 5 generic-reversal guard, 14 Sep 2026, commits 833ebfb1 (code) + V254

**Clean measurement first (MASTER's stop-condition):** production reverses through its OWN helper `production.py:_reverse_journal`, which INHERITS the original's `source_type` (PRODUCTION_*), and is called internally by `cancel_order` / month-end-reconcile void — it NEVER calls `POST /api/journals/{id}/reverse`. So the endpoint's allowlist does not break any production flow. The 65 MANUAL reversals (source_type=MANUAL) were manual/bot clicks through the generic endpoint, not production's doing. → proceeded.

**Endpoint `POST /api/journals/{id}/reverse`:**
- ALLOWLIST (fail-closed): MANUAL, ADJUSTMENT, RECONCILIATION_ADJUSTMENT, RECLASSIFY_CN_COGS_GAP, RECLASSIFY_TAX_D1_DRIFT. REVERSAL excluded (Law 26). A new source_type is rejected by default.
- A document-owned journal → 400 with per-type guidance pointing at the REAL void (bill/invoice/expense/payment; production → "batalkan work order POST /api/production/{id}/cancel atau void rekonsiliasi"; etc — endpoints verified to exist).
- reverse-of-reversal → 400 (target `reversal_of_id IS NOT NULL`).
- `validate_no_derived_layer_accounts()` now runs on the reverse path too (was create-only) — a manual reversal can't newly touch AR/PAYABLE/inventory/COGS.
- kept: already-reversed → 409, closed period → 403.

**V254 `trg_prevent_reverse_of_reversal`** (BEFORE INSERT ON journal_entries, Law 13/26): a reversal whose target is itself a reversal → check_violation. Historical reverse-of-reversal = 0 (measured all tenants). Corrects the ironlaws skill's false claim that this trigger already existed.

**Gate `scripts/gerbang_reverse_guard.py`:** baru+v254 10/10 — MANUAL 200; BILL/INVOICE/PRODUCTION 400 with correct void guidance; reverse-of-reversal 400; double 409; PAYABLE-touching reversal 400; trigger direct-INSERT 23514. Old: BILL/INVOICE/PRODUCTION reverse LOLOS (gap proven). Sabotage disable allowlist → RED (the specific "void X" message gone; the derived-layer guard still catches doc journals that touch derived accounts, but that's the second layer, not the primary). Live: 8/8. Lossless ROLLBACKs.

**65 historical MANUAL reversals + the 9 pinned bills: untouched.**

**Follow-up (FRONTEND/bot, MASTER forwards):** the FE Journal Entry detail "void" button (useJournalDetail.ts) now gets a 400 for document-owned journals → hide it there or surface the message; the bot `reverse_journal` direct action now returns a readable 400 for document journals (not silent) → ideally route document reversals to the `void_*` actions.


## Bot resolver (Option A) LIVE 884ece56
reverse_journal atas jurnal milik dokumen -> klarifikasi terbaca menunjuk void dokumennya (nomor dari DATA via source_id; dokumen tak ada -> source_type saja); jurnal manual tetap lanjut ke reverse. 0 pending untuk dokumen. Gate scripts/gerbang_reverse_resolver.py 6/6 (BILL-2609-0008/INV-2608-0001/WO-2026-000003), lama = celah. (B) auto-swap ke void_* TIDAK dikerjakan (putusan MASTER) — jalur void_* di registry tetap dipanggil manual oleh user.
