# ROLLBACK — GERBANG ENTITAS FASE 1b

Branch: `feat/gerbang-quote-si-so` (worktree /root/mh-gerbang1b)
Basis: master `09e4441d` — TIDAK di-merge oleh agen. Merge+deploy = langkah manusia.

## Commit batch ini
(diperbarui tiap commit)
- `beb67a97` feat(gerbang): perluas radius gerbang entitas ke quote/SI/SO (Fase 1b)

## Langkah darurat (SESUDAH deploy, kalau produksi rusak)
git -C /root/milkyhoop-dev reset --keep 09e4441d
docker restart milkyhoop-dev-api_gateway

JANGAN `compose up --force-recreate`.
JANGAN `git reset --hard` (75 entri kotor di /root/milkyhoop-dev bukan milik batch ini).

## Radius perubahan
- backend/api_gateway/app/services/unified_agent/gerbang_entitas.py
- backend/api_gateway/tests/unit/test_gerbang_entitas.py
Tidak ada perubahan di tool_executor.py (situs pemasangan sudah action-agnostic).
