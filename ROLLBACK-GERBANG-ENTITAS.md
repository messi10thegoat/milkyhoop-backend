# ROLLBACK — GERBANG ENTITAS FASE 1a

Branch: `feat/gerbang-entitas` (dari `master` = e6b44a34).
BELUM di-merge, BELUM di-deploy.

## Commit yang dibuat batch ini
- 57c53be1b5829258c4eaca945b485063bfb8ec62 — gerbang entitas create_bill + gate unit (38 hijau / 4 merah di e6b44a34)

## Cara membatalkan
Belum di-merge → cukup:
```
git -C /root/milkyhoop-dev worktree remove --force /root/mh-gerbang
git -C /root/milkyhoop-dev branch -D feat/gerbang-entitas
```
Kalau SUDAH ter-merge dan sudah live, urutannya:
```
git -C /root/milkyhoop-dev revert --no-commit <SHA>
git -C /root/milkyhoop-dev commit --no-verify -m "revert(gerbang-entitas)"
docker compose up -d --force-recreate api_gateway   # LANGKAH MANUSIA
```
Tidak ada migrasi DB, tidak ada tulisan DB, tidak ada perubahan skema.
Membatalkan = kembali ke perilaku lama: kartu create_bill tetap dibangun
walau vendor/barang belum terdaftar.

## Berkas yang disentuh
- `backend/api_gateway/app/services/unified_agent/gerbang_entitas.py` (BARU)
- `backend/api_gateway/app/services/unified_agent/tool_executor.py` (1 sisipan)
- `backend/api_gateway/app/services/unified_agent/orchestrator.py` (cabang CLARIFICATION)
- `backend/api_gateway/tests/unit/test_gerbang_entitas.py` (BARU)
