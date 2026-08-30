# T181 Langkah 1 — Rencana Rollback (ditulis SEBELUM menyentuh kode)

Baseline master saat rencana ini ditulis: fc351067
Branch kerja: feat/t181-instrumentasi (worktree /root/mh-t181)
Sifat perubahan: LOG-ONLY (hanya logger.* + variabel lokal). Nol perubahan perilaku.

## Kalau instrumentasi ini bikin kacau (traceback / kebisingan / regresi)

1. Kembalikan main tree ke baseline:
   git -C /root/milkyhoop-dev reset --keep fc351067      # BUKAN --hard (tree kotor 75 entri milik orang lain)
2. Muat ulang proses:
   docker restart milkyhoop-dev-api_gateway
3. Buktikan rollback benar-benar terjadi:
   git -C /root/milkyhoop-dev rev-parse master           # harus fc351067
   docker inspect -f '{{.State.StartedAt}}' milkyhoop-dev-api_gateway   # harus BERGESER
   docker logs --since 5m milkyhoop-dev-api_gateway 2>&1 | grep -c 'MERGE_ITEMS'   # harus 0

## Kalau reset --keep menolak (ada perubahan lokal yang bentrok)
   git -C /root/milkyhoop-dev stash push -- backend/     # HANYA backend; frontend/ kotor milik orang lain
   git -C /root/milkyhoop-dev reset --keep fc351067

## Membuang worktree ini
   git -C /root/milkyhoop-dev worktree remove /root/mh-t181
   git -C /root/milkyhoop-dev branch -D feat/t181-instrumentasi

## Catatan
- JANGAN pernah git reset --hard di /root/milkyhoop-dev.
- Kekotoran tree yang ADA SEBELUM pekerjaan ini: 75 entri (frontend/ + 2 .env.bak). Bukan milik T181.
