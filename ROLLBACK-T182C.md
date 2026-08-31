# ROLLBACK T182-C — items sebagai ARRAY untuk create_quote + create_sales_order

Branch: feat/t182c-array-quote-so (worktree /root/mh-t182c)
Basis: master 1f7d2358d2391a1c068eb71ad45951dfc282e4b6

## Commit yang termasuk tiket ini (daftar LENGKAP, diisi saat dibuat)
1. <SHA-1> feat(t182c): items ARRAY untuk create_sales_order
2. <SHA-2> feat(t182c): items ARRAY untuk create_quote

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
