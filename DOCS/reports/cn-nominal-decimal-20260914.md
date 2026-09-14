# Credit Note nominal Decimal (Law 25) — LIVE

**Commit:** `dbea1db2` (deploy/master) · **Tanggal:** 2026-09-14 · **Gateway:** healthz 200

## Masalah (terukur)
Kolom DB `credit_notes`/`credit_note_items` sudah `numeric(18,2)` (benar). Pembulatan ada di **lapis
Python**: DTO nominal `int`, dan `calculate_item_totals` mengembalikan `int(subtotal/discount/tax/total)`
+ `overall_discount/tax = int(...)`. → nominal 100000.50 dipangkas jadi 100000.

## Perbaikan (nuance Law 25)
Prinsip: **Decimal masuk/hitung/DB; `float` HANYA di batas respons Pydantic** (Decimal di field respons
ter-serialize jadi STRING → merusak pembacaan numerik FE).
- **REQUEST** nominal `int`→`Decimal`: `unit_price`, item+overall `discount_amount`, apply `amount`,
  refund `amount` → terima 100000.50 utuh.
- **calc + overall discount/tax**: `int()`→`_q2` (Decimal `quantize(0.01, ROUND_HALF_UP)`) → presisi
  dijaga sepanjang pipa ke DB numeric.
- `get_invoice_remaining_from_journal` → `Decimal` (bukan `int`).
- **RESPONSE** nominal `int`→`float` (tetap **ANGKA JSON**, bukan Decimal-string; `List.total`=cacah tetap `int`).

## Kontrak FE
Sekarang: respons nominal = angka JSON (mis. `"subtotal":100000.5`). Sebelum: angka JSON bulat
(`100000`). **Tipe JSON tetap number** — FE membaca number seperti sebelumnya; hanya desimal kini muncul.
Tidak ada perubahan ke Decimal-string.

## Gerbang dua-sisi (kontainer, calc+schema NYATA, baru-vs-lama) — 6/6 GREEN
- A calc: BARU 100000.50 utuh vs LAMA 100000 (dibulatkan, RED).
- B request: BARU `Decimal` terima 100000.50 vs LAMA `int` → ValidationError (RED).
- C respons: BARU `subtotal` = angka JSON `100000.5` (bukan string, bukan bulat) vs LAMA `int` tolak
  desimal (RED).
