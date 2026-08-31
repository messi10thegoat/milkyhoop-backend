# ROLLBACK T182-D — items ARRAY + gerbang entitas untuk create_stock_adjustment

Branch: feat/t182d-stockadj (worktree /root/mh-t182d)
Basis: master 4bec354ad6d6fa48697f3e8d2f74a3a42daa8125

## Commit yang termasuk tiket ini (daftar LENGKAP, diisi saat dibuat)
1. (A) <diisi> feat(t182d): items ARRAY untuk create_stock_adjustment
2. (B) <diisi> feat(t182d): create_stock_adjustment masuk radius gerbang entitas
3. (C) <diisi> docs(rollback): tutup daftar commit T182-D

Berkas yang disentuh:
- backend/api_gateway/app/services/unified_agent/direct_action_registry.py
  (HANYA FieldSpec name="items" milik create_stock_adjustment)
- backend/api_gateway/app/services/unified_agent/gerbang_entitas.py
  (HANYA penambahan satu entri PETA_AKSI: create_stock_adjustment)
- backend/api_gateway/tests/unit/test_skema_items.py (tes + AKSI_TAK_DIUBAH)
- backend/api_gateway/tests/unit/test_gerbang_entitas.py (tes gerbang)
- ROLLBACK-T182D.md (berkas ini)

## Keadaan saat ditulis
- BELUM di-merge ke master. BELUM di-deploy. Produksi masih menjalankan 4bec354a.
- Selama belum merge, ROLLBACK = TIDAK PERLU melakukan apa pun.

## Langkah darurat (HANYA bila sudah ter-merge ke master lalu produksi rusak)
    git -C /root/milkyhoop-dev reset --keep 4bec354a
    docker restart milkyhoop-dev-api_gateway

JANGAN pakai `docker compose up --force-recreate`.
JANGAN `git reset --hard` (pohon utama punya 75 entri kotor milik sesi lain).

## Rollback SEBAGIAN (kalau hanya salah satu perubahan yang rusak)
Dua perubahan sengaja dipisah jadi dua commit supaya bisa dicabut sendiri-sendiri:
    git revert --no-edit <SHA-B>   # cabut gerbang saja, array tetap
    git revert --no-edit <SHA-A>   # cabut array saja, gerbang tetap

## Membuang worktree ini seluruhnya
    git -C /root/milkyhoop-dev worktree remove --force /root/mh-t182d
    git -C /root/milkyhoop-dev branch -D feat/t182d-stockadj

## Sifat perubahan
Deklaratif saja: mengisi FieldSpec.item_schema + menambah satu entri peta.
NOL perubahan skema DB, NOL migrasi, NOL tulisan DB, NOL perubahan pada
badan fungsi periksa_gerbang_entitas, NOL perubahan jalur hilir.
