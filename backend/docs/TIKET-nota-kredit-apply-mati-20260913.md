# TIKET — terapkan nota kredit ke faktur: fitur MATI + atribusi piutang (13 Sep 2026)

**Status:** TERBUKA. **Putusan desain A/B menunggu pemilik** (pertanyaan produk: bolehkah satu
nota kredit dipecah ke beberapa faktur?). Nol kode.

## 1. Fitur mati — TERUKUR lewat eksekusi

`credit_notes.py` ~1504: `invoice["customer_id"] != cn["customer_id"]`.
`sales_invoices.customer_id` = **uuid**, `credit_notes.customer_id` = **varchar** → asyncpg
memberi `UUID` vs `str` → **selalu tidak sama** → **selalu 400 "belongs to different customer"**
bila nota kredit punya pelanggan. Dengan pelanggan yang teksnya identik, tetap 400 — dengan dan
tanpa pagar V244. `credit_note_applications` = **0 baris**: tak pernah berhasil sekali pun.

## 2. Premis yang SALAH dan dicabut

- **"Perbaikan tipe data"** bukan bentuk masalahnya (lihat 3).
- **"Samakan dengan pola DP"**: `apply_customer_deposit` **TIDAK memeriksa pelanggan sama
  sekali**. Menyamakannya = mencabut pemeriksaan pelanggan.
- **"AR efektif turun sebesar yang diterapkan"**: SALAH. Apply **tidak membuat jurnal**; piutang
  **sudah dikredit saat nota kredit DIPOSTING** (Dr REVENUE / Cr RECEIVABLE). Gerbang yang benar:
  saat apply **AR efektif TIDAK berubah**, yang berubah hanya **atribusinya** ke faktur. Gerbang
  yang menuntut AR turun akan memaksa jurnal kedua = hitung-ganda yang dilegalkan.

## 3. Atribusi — kenapa membetulkan tipe saja MERUSAK

`compute_ar_outstanding` menghitung kredit CN **hanya lewat `credit_notes.original_invoice_id`**,
bukan lewat `credit_note_applications`. Membetulkan perbandingan saja → apply menaikkan cache
`amount_paid` faktur sementara `compute_ar_outstanding` tetap outstanding penuh → **cache ≠ ledger
sejak lahir** (ARAP Rule 12).

## 4. Selisih AR HARI INI — TERUKUR

GL RECEIVABLE efektif vs Σ `compute_ar_outstanding`:

| tenant | GL | Σ per faktur | selisih | = |
|---|---|---|---|---|
| kaos-biru-konveksi | 2.260.000 | 2.285.000 | 25.000 | CN-2609-0006 (tanpa faktur asal) |
| grapgrap-manado | −180.000 | 20.000 | 200.000 | CN-2608-0001 (tanpa faktur asal) |

**Laporan piutang per faktur hari ini LEBIH BESAR dari buku besar** sebesar kredit nota kredit
yang tak terkait faktur.

`verify_ar_reconciliation_all()` **PASS** di atas selisih itu (total_gl = canonical). Definisi
GL-nya berbeda dari kueri Rule 8 skill arap. **Belum dibaca — belum diklaim buta.** Tiket kerja:
baca definisinya, buktikan buta/tidak lewat eksekusi (seperti R9).

## 5. Pilihan desain (pemilik)

- **A.** Apply mengaitkan kredit CN yang sudah diposting tanpa jurnal baru; `compute_ar_outstanding`
  diperluas menghitung `credit_note_applications` + dedup dgn `original_invoice_id`. Menyentuh
  fungsi Rule 5 (dashboard, aging, bot) — gerbang Rule 8 + semua pembaca.
- **B.** Apply mengisi `credit_notes.original_invoice_id` bila kosong (satu faktur per CN); fungsi
  tak berubah; menyunting kolom kaitan dokumen posted (bukan nominal).

## 6. DUGAAN terkait — DP lintas pelanggan

`apply_customer_deposit` tak memeriksa pelanggan → **DUGAAN**: DP pelanggan A bisa diterapkan ke
faktur pelanggan B (menulis ke piutang pelanggan yang salah). **Naikkan lewat eksekusi di
ROLLBACK.** Bila terbukti, prioritasnya di atas nota kredit.

---

# TAMBAHAN 13 Sep 2026 — putusan pemilik + penghalang

- **Putusan pemilik: opsi B — satu nota kredit, satu faktur.** `compute_ar_outstanding` TIDAK diubah.
  Syarat bentuk (MASTER): isi `original_invoice_id` HANYA dari NULL (`WHERE original_invoice_id IS
  NULL` di pernyataannya); pertimbangkan pagar DB beku-sekali-terisi (enumerasi penulis sah dulu);
  pihak kanonik; **AR efektif TIDAK berubah, tanpa jurnal baru**; assert selisih GL vs Σ compute
  menutup persis sebesar CN; cache sepakat dgn compute (ARAP Rule 12); **2 CN historis JANGAN
  dikaitkan otomatis**; batas melebihi outstanding / CN sudah terkait → tolak; sesudah hidup **ulang
  gerbang V244 jalur 6 ujung-ke-ujung**.
- **Penghalang (terukur):** 1 dari 2 CN posted ber-`customer_id` **bukan UUID** → helper
  `pihak_helpers` akan menolaknya. Ukur nilai, jalur pembuat, apakah masih hidup, sebelum B.
- DP lintas pelanggan (butir 6 di atas): **TERBUKTI lalu TUTUP `fe42de6a`** — lihat
  `TEMUAN-dana-ke-dokumen-pihak-lain-20260913.md`.
- `verify_ar_reconciliation_all()` (butir 4): **terbukti buta** — `TIKET-verify-ar-reconciliation-buta-20260913.md`.


---

## RESOLUSI — Unit B live (14 Sep 2026, commit `49d49152`)

**Shape (owner's decision: one credit note, one invoice):** the credit note is applied at its FULL amount to exactly one invoice. Attribution uses compare-and-set on `credit_notes.original_invoice_id` from NULL. No new journal (the receivable was already credited when the CN was posted). The invoice + `accounts_receivable` cache is recomputed from `compute_ar_outstanding`.
Also included in the unit:
- (a) draft create/PATCH + post validate the invoice by tenant AND customer;
- (b) voiding an invoice with a linked CN → 400;
- (c) posting/voiding a linked CN recomputes the cache.

**Why the closure had to stay until B:** with the closure removed, the OLD apply returned 200 but was silently wrong. It never wrote `original_invoice_id`, and it computed the cache with its own arithmetic (120,000 vs compute 100,000). The CN became 'applied' while its receivable was still unattributed.

**Live gate (after restart; unit B run on a byte-identical copy of the live module, md5 `1c32b4e7…`):**
- `gerbang_unit_b_cn.py` 28/28:
  - GL AR unchanged; GL−Σcompute gap closes EXACTLY by the CN total; zero journals;
  - cache + accounts_receivable == compute, including an apply that settles the invoice (PAID);
  - 9 owner-readable rejections.
- Sabotage (original_invoice_id write neutralised, apply still 200): the gap check turns RED.
- `gerbang_v244_handler.py` 18/18 on the live module: the narrow path-6 exemption is REMOVED. Before deploy, the live module (closure) was RED on path 6; after deploy it is green.
- **Existence indistinguishability:** another tenant's invoice id vs a made-up id → exception type + status + repr(detail) + headers identical **at the HTTPException level**. It is NOT measured as HTTP bytes: there were no credentials for a real HTTP call.

**Owner decisions:**
- CN-2608-0001 (grapgrap 200,000) is left AS IS: the only Toko Melati invoice with a remaining balance has 20,000, so it can't be applied. Candidate for a "known" pin together with the `verify_ar_reconciliation_all` decision (not pinned in B).
- CN-2609-0006 is NOT linked automatically; which invoice is the owner's choice, through the feature.

**OPEN WORK (not a final design):**
1. **Un-apply doesn't exist.** An applied CN–invoice pair is **locked both ways**: the CN can't be voided (existing guard) and the invoice can't be voided (guard B). This is consistent with Law 2, but the owner will run into it. Zero linked pairs today.
2. **`ApplyCreditNoteItem.amount` is `int`** → a CN with a fractional total can never be applied. Law 25 requires `Decimal` for DTO inputs; this is a correctness defect, not just tidiness. Measured: all CNs today are whole numbers.
3. **FE ApplySheet** still sends multi-select + partial amounts → clear 400 until the FRONTEND unit.
4. DB fence `original_invoice_id` (filled + not draft → frozen): separate migration. 3 legitimate writers measured, all compatible.
5. Measurement limit for `accounts_receivable`: the comparison against compute only covers rows compute emits (outstanding ≠ 0); PAID/VOID rows aren't compared. A missing row is treated as outstanding 0 (same as the receive-payment helper).

**Note for future gates:** invoice `9eaa85d2` (RAHAYU UMAR) also has a customer deposit APPLIED, so the deposit guard would block its void too. Assert (b) checks the text "nota kredit terkait", so it can't be masked by the other guard; any gate that picks this subject must discriminate by text/type, not just status 400.


---

## UNIT (2) — un-apply live (14 Sep 2026, commit `a6cd0194`, migration V249)

Closes OPEN WORK item 1 ("Un-apply doesn't exist; the pair is locked both ways").

- `POST /api/credit-notes/{id}/unapply {reason}`: **no journal** (apply has none).
  - `original_invoice_id` → NULL via compare-and-set from the exact invoice.
  - The application row → `reversed` (+reversed_at/by/reason, **not deleted**).
  - The cache is recomputed from compute.
  - Audit `CREDIT_NOTE_UNAPPLIED`.
- Owner decisions: **reason required**; application date in a **CLOSED** period → 400.
- V249: `credit_note_applications.status` + CHECK. Trigger `update_credit_note_status` sums **active** rows only.
- Attributing readers filter `status='active'`. **A B-era display bug is fixed:** invoice detail `applied_credits` listed only `cn.status='posted'`, so an applied CN (status `applied`) vanished from its invoice's detail.
- Void locks open by themselves after un-apply; proven through the handlers.
- AR checker V248: after un-apply, residual −amount appears (true red) until it is applied again. No checker change.

Gate `scripts/gerbang_unapply_cn.py`:
- 20/20 new and after install;
- old: 16 RED;
- sabotage — trigger sums all rows / link kept / period guard dropped — each caught with un-apply still 200.
- After deploy: unit B 28/28 and V248 14/14 still green.

**Remaining:** fence (e) `original_invoice_id` must allow the non-null→NULL transition only together with an application changing to reversed in the same transaction. FE "Batalkan penerapan" button (FRONTEND).


---

## FENCE (e) V250 live (14 Sep 2026, commit 22509138)

For a non-draft CN, `original_invoice_id` can only change together with its application history in the same transaction:
- NULL->X needs an active application;
- X->NULL needs a reversal in this transaction;
- X->Y is always rejected (23514).

The apply/unapply handlers were reordered (application first, link second). Deploy order: code first, then V250.

Gate `scripts/gerbang_pagar_v250.py` 13/13, live 13/13:
- old version (no fence): 4 RED;
- fence + old order: apply 500;
- two sabotages caught. The first release sabotage failed as a tool (AND/OR precedence) and was fixed.

After live: unapply 20/20, unit B 28/28, V244 18/18.
