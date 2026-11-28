# Kontrak BUG-451 — satuan diketik di Kirim Faktur / SO-fulfill (28 Nov 2026)
Penentu SATU: `services/satuan_kirim.py` (pratinjau == tulis). FE tidak menghitung konversi.

Masukan (aditif, opsional): `items[].unit` pada POST `/api/sales-invoices/{id}/fulfill(/preview)` dan `items[].unit` pada POST `/api/sales-orders/{id}/fulfill(/preview)`.
Kosong = perilaku lama (quantity dalam satuan baris). `quantity` = jumlah dalam `unit` yang diketik; server mengonversi ke satuan BARIS lewat satuan dasar (peti→pcs→baris dus, satu penentu).
`batch_id`/`serials` tetap berlaku (kuantitas hasil konversi).

Respons per `lines[]` (hanya bila unit diketik): `unit_input`, `quantity_input` (teks), `quantity_text_input` (mis. "1 peti = 120 pcs"). `quantity`/`quantity_text`/`unit` tetap = satuan baris.

Galat: satuan tak dikenal → kode `SATUAN_TAK_DIKENAL`, pesan `Satuan "peti" tak dikenal — <barang> tak punya satuan peti; ada: pcs, dus`.
Pratinjau: HTTP 200, `blocks[]` (+ `invoice_item_id` / `sales_order_item_id`). Tulis faktur: 422 berkode (`code`,`message`,`field=items`). Tak habis dibagi (4 desimal) → `SATUAN_TIDAK_HABIS_DIBAGI`; tanpa konversi → `SATUAN_TAK_TERKONVERSI`.
Catatan: jalur tulis SO-fulfill memakai rencana yang sama dengan pratinjaunya (blok = 4xx lewat rencana_kirim_so).
