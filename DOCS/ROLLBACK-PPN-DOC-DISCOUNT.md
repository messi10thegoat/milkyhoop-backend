# ROLLBACK — kalkulator PPN/diskon bersama (Faktur, SO, SO→Faktur)

Unit dikirim sebagai **dua commit**, dan pembagian itu ADALAH rencana rollback-nya:

| Commit | Isi | Saat rollback |
|---|---|---|
| **A — maju-saja** (commit ini) | V288 (`sales_invoices.shipping_amount`, `sales_order_items.dpp`, pagar Law-19 + `shipping_amount`) · model respons SO `int → float` (subtotal/tax_amount/total_amount/line_total) · dokumen ini | **TETAP. Jangan pernah di-revert** setelah B pernah hidup. |
| **B — kalkulator** | `services/sales_doc_calc.py` + pemasangannya di `sales_invoices.py` / `sales_orders.py` | **Inilah yang di-revert.** |

## Kenapa A tidak boleh ikut mundur

Begitu B hidup, SO bisa menyimpan PPN bersen (mis. 1.895.525,50). Model respons SO lama bertipe `int`,
dan Pydantic v2 MENOLAK angka berpecahan untuk `int` → **setiap GET SO itu 500**. Terbukti sebelum
deploy: GET SO versi lama pada SO ber-sen → `int_from_float` → 500. Selama A tetap, kode lama (sesudah
B di-revert) tetap bisa MEMBACA dokumen yang dibuat B.

V288 aditif: kolom baru diabaikan kode lama (`shipping_amount` DEFAULT 0, `dpp` boleh NULL); pagar
Law-19 hanya menambah satu kolom beku. Tidak ada alasan membuangnya, dan membuangnya menghapus data.

## Sebelum me-revert B — ukur dulu (psql milkydb, baca saja)

```sql
-- 1) Draf faktur yang MEMBAWA ONGKIR (lahir dari SO→Faktur di bawah B).
--    Kode lama tidak mengenal ongkir: simpan-ulang draf ini sesudah revert MENGHITUNG ULANG total
--    TANPA ongkir -> ongkir hilang diam-diam. Bila > 0: posting/void dulu, atau catat & perbaiki manual.
SELECT tenant_id, invoice_number, shipping_amount FROM sales_invoices
WHERE status = 'draft' AND shipping_amount > 0;

-- 2) Draf yang PPN-nya dihitung aturan baru (diskon dokumen dialokasikan). Sesudah revert, simpan
--    berikutnya menghitung ulang dengan aturan LAMA (PPN sebelum diskon dokumen = lebih besar).
--    Dokumen yang sudah dibukukan TIDAK berubah (Law 19).
SELECT tenant_id, invoice_number, discount_amount, tax_amount FROM sales_invoices
WHERE status = 'draft' AND discount_amount > 0 AND tax_amount > 0;

-- 3) SO ber-sen: ALASAN A harus tetap. Bila > 0 dan A di-revert -> GET SO 500.
SELECT count(*) FROM sales_orders
WHERE subtotal <> trunc(subtotal) OR tax_amount <> trunc(tax_amount) OR total_amount <> trunc(total_amount);
```

## Prosedur

1. Jalankan pengukuran di atas; putuskan untuk draf di (1).
2. Di main tree BE: `git revert --no-edit <sha-B>` → `git push deploy master`.
3. `scripts/ops/mh-restart.sh api_gateway` (TIDAK ada migrasi mundur).
4. Verifikasi: StartedAt bergeser + `/healthz` 200 lewat HTTP; `grep -c compute_document`
   di `/app/backend/api_gateway/app/routers/sales_invoices.py` dalam kontainer = 0;
   GET satu SO ber-sen (query 3) = 200.

## Yang TIDAK dipulihkan oleh revert B
- Dokumen yang sudah dibukukan dengan aturan baru tetap seperti dibukukan (Law 19: koreksi = void + dokumen baru).
- Faktur dari SO→Faktur di bawah B tetap membawa diskon/ongkir dan `dpp` per baris.
