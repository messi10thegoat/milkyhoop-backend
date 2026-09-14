# [B] Penegakan izin document-intake / documents — LIVE

**Commit:** `1df2ec63` (deploy/master) · **Tanggal:** 2026-09-14 · **Gateway:** healthz 200

## Apa yang ditegakkan
Izin diambil dari **doc_type TERSIMPAN** (`uploaded_documents.doc_type`, difilter `tenant_id`),
**bukan** dari body klien. Peta `_DOCTYPE_MODULE` → modul-tujuan (semua resolve nyata di policy engine):

| doc_type | modul | can(C) diperlukan |
|---|---|---|
| sales_invoice/invoice/tax_invoice/faktur_pajak | INVOICE | ✓ |
| receipt/sales_receipt/nota/kwitansi/payment_receipt | RECEIPT | ✓ |
| bill/purchase_invoice/purchase/vendor_invoice | BILL | ✓ |
| expense | EXPENSE | ✓ |
| bank_transfer | BANK | ✓ |
| **tak dikenal / NULL** | — | **403 fail-closed + log** |

### Per handler
- **confirm / execute** → `_require_doc_create_perm(request, tenant, doc_id)`: anggota aktif + `can(C, modul-tujuan)` dari doc_type tersimpan; tak dikenal/NULL → 403 fail-closed.
- **execute-batch** → cek **per item**; item tanpa izin/tak dikenal/lintas-tenant → `denied[]` (403 per item); hanya yang berizin dieksekusi. Batch tak pernah lolos diam-diam.
- **upload / reject / retry** (intake) & **upload / attach** (documents) → cukup **anggota AKTIF** (`membership_active`).
- **middleware**: 6 rute → `WRITE_EXEMPT` ("izin ditegakkan di handler"), dihapus dari `izin_write_baseline.json` (37→29). Rute intake lain (cleanup/process/retry-all-failed) tetap owner-only floor.

## Gerbang dua-sisi (kontainer, savepoint ROLLBACK, engine+DB nyata) — 7/7 GREEN
Identitas nyata: Admin(grapgrap-manado, bill=T), Collaborator(kaos-biru-konveksi, invoice=T **bill=F**).
- S1 ADMIN confirm bill → **LOLOS** vs S2 Collaborator confirm bill → **403** (fungsi SAMA membedakan peran)
- S3 body-tamper bill→invoice tak menolong (baca tipe TERSIMPAN; fungsi tak punya param body) → 403
- S4 ADMIN tipe fabrikasi → 403 fail-closed · S5 doc_type NULL → 403 fail-closed
- S6 batch campur → exec hanya [invoice]; denied = bill/fabrikasi/**lintas-tenant** (403 per item)
- RED-alat: modul LAMA tanpa `_require_doc_create_perm` (hasattr=False)

## Ratchet WRITE-floor (gerbang_tahap3.py, middleware+engine nyata)
- `baru`: **8/8 PASS** (CAKUPAN bersih, baseline=29, non-owner unmapped→403, exempt→lolos, read→terbuka)
- `sabotase_hapus_pola`: **2 gagal = TERTANGKAP** (alat bisa merah)
