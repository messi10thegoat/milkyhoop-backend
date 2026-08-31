# ROLLBACK T187 — pagar blok T171_SISA_BULK

Branch: feat/t187-pagar-items (worktree /root/mh-t187)
Basis: master 07a8ba92dfb7cdccb0fde8852c5980b62d06ce66

## Commit yang dibuat tiket ini
(diisi setelah commit; lihat `git -C /root/mh-t187 log --oneline master..HEAD`)
- 24b286cbf65aecdd2d1f69e8e7df7967f489dc08 pagar action_key pada blok T171_SISA_BULK + tes unit T187

Tak satu pun commit ini di-merge ke master. Produksi TIDAK disentuh.

## Kalau sudah terlanjur di-merge dan produksi rusak
    git -C /root/milkyhoop-dev reset --keep 07a8ba92
    docker restart milkyhoop-dev-api_gateway

JANGAN `compose up --force-recreate`. JANGAN `git reset --hard`.

## Buang worktree
    git -C /root/milkyhoop-dev worktree remove /root/mh-t187 --force
    git -C /root/milkyhoop-dev branch -D feat/t187-pagar-items
