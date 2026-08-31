# ROLLBACK T182-C — items sebagai ARRAY untuk create_quote + create_sales_order

Branch: feat/t182c-array-quote-so (worktree /root/mh-t182c)
Basis: master 1f7d2358d2391a1c068eb71ad45951dfc282e4b6

## Commit yang termasuk tiket ini (daftar LENGKAP, diisi saat dibuat)
1. e2318c56 feat(t182c): items ARRAY untuk create_sales_order
2. de8071bc feat(t182c): items ARRAY untuk create_quote

Berkas yang disentuh:
- backend/api_gateway/app/services/unified_agent/direct_action_registry.py
  (HANYA FieldSpec name="items" milik create_sales_order dan create_quote)
- backend/api_gateway/tests/unit/test_skema_items.py (tes + AKSI_TAK_DIUBAH)
- ROLLBACK-T182C.md (berkas ini)

## Keadaan saat ditulis
- BELUM di-merge ke master. BELUM di-deploy. Produksi masih menjalankan 1f7d2358.
- Selama belum merge, ROLLBACK = TIDAK PERLU melakukan apa pun.

## Langkah darurat (HANYA bila sudah ter-merge ke master lalu produksi rusak)
    git -C /root/milkyhoop-dev reset --keep 1f7d2358
    docker restart milkyhoop-dev-api_gateway

JANGAN pakai `docker compose up --force-recreate`.
JANGAN `git reset --hard` (pohon utama punya 75 entri kotor milik sesi lain).

## Membuang worktree ini seluruhnya
    git -C /root/milkyhoop-dev worktree remove --force /root/mh-t182c
    git -C /root/milkyhoop-dev branch -D feat/t182c-array-quote-so

## Sifat perubahan
Deklaratif saja: mengisi FieldSpec.item_schema sehingga build_intent_schema
mengambil cabang array. NOL perubahan skema DB, NOL migrasi, NOL tulisan DB,
NOL perubahan jalur hilir (enricher/scalar-fallback/gerbang entitas).

3. 7b62f5e2 docs(rollback): tutup daftar commit T182-C
   (commit ini sendiri — hanya berkas ROLLBACK-T182C.md)

## Bukti gate A (2026-08-31)
- Harness: 83 baseline -> 85 passed di branch.
- Bukti MERAH bermakna: worktree detached di 1f7d2358 dengan berkas tes
  DIBAWA tapi pemasangan TIDAK dibawa -> 4 failed, 81 passed, NOL error
  impor. Alasan gagal = AssertionError "items dideklarasikan sebagai
  ['string', 'null'], bukan 'array'". Worktree bukti sudah dihapus.
- DIFF SKEMA seluruh 61 aksi: TEPAT 2 berubah (create_quote,
  create_sales_order); 59 byte-identik, termasuk create_bill,
  create_sales_invoice, create_stock_adjustment, create_journal_entry,
  dan kesembilan update_*.
