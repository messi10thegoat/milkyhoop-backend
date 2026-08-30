# Rencana Rollback — TIKET SALAH-RUTE FASE 1 (batas kata ketat)

Tanggal tulis: 2026-08-30 (SEBELUM berkas kode apa pun disentuh)
Baseline master: c1aa73d42156f42f06f6f116aa760f8e5200da41
Worktree: /root/mh-salahrute  branch: feat/salahrute-batas-kata
Berkas yang akan disentuh:
- backend/api_gateway/app/services/unified_agent/entity_extractor.py (classify_crud_intent, Step 2)
- backend/api_gateway/app/services/unified_agent/orchestrator.py (penanda [SHADOW], LOG-ONLY)

## Rollback SEBELUM merge (ronde ini)
git -C /root/milkyhoop-dev worktree remove --force /root/mh-salahrute
git -C /root/milkyhoop-dev branch -D feat/salahrute-batas-kata
Efek: NOL. master tetap c1aa73d4, live tree tak tersentuh.

## Rollback SESUDAH merge+deploy (dijalankan OWNER)
1) git -C /root/milkyhoop-dev reset --keep c1aa73d4     # kembali ke baseline
   (JANGAN --hard: tree frontend/ kotor 75 entri sejak deploy FE 8 Agt)
2) docker restart milkyhoop-dev-api_gateway
3) Bukti rollback: docker inspect -f "{{.State.StartedAt}}" milkyhoop-dev-api_gateway bergeser
   DAN "catat pembelian dari PT Sinar ..." kembali salah-rute ke create_customer di milkyhoop.com

## Rollback parsial (hanya Langkah 1, pertahankan Langkah 2 log)
git -C /root/milkyhoop-dev revert --no-edit <sha-langkah-1>

## Yang TIDAK berubah
- NOL tulisan DB, NOL migrasi, NOL perubahan skema -> tidak ada rollback DB.
- guard_arbiter.py / PolicyType.ALWAYS_WIN tidak disentuh.
