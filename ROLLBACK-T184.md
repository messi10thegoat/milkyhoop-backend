# ROLLBACK — T184 kartu jurnal memperlihatkan barisnya

Ditulis SEBELUM kode disentuh. Diperbarui dengan SHA sesudah commit.

## Titik aman
- master produksi: `4bec354ad6d6fa48697f3e8d2f74a3a42daa8125`
- worktree kerja: `/root/mh-t184`, branch `feat/t184-kartu-jurnal`
- BELUM di-merge. Produksi TIDAK menerima perubahan ini.

## Commit di branch ini
- `6c324f991547dddef940724d83cb8b62e03d15a6` — fix(chat): kartu jurnal umum memperlihatkan barisnya sebelum tombol ditekan (satu-satunya commit KODE)

- `06ad5b10` — docs(rollback): isi SHA commit T184 (berkas ini saja, nol kode)

## Berkas yang disentuh
- `backend/api_gateway/app/services/unified_agent/direct_action_registry.py`
- `backend/api_gateway/app/services/unified_agent/tool_executor.py`
- `backend/api_gateway/tests/unit/test_kartu_jurnal.py` (BARU)

## Langkah darurat (kalau sudah terlanjur di-merge + deploy)
```
git -C /root/milkyhoop-dev reset --keep 4bec354ad6d6fa48697f3e8d2f74a3a42daa8125
docker restart milkyhoop-dev-api_gateway
```
JANGAN `docker compose up --force-recreate` (compose stale; redis auth putus).

## Membuang worktree ini
```
git -C /root/milkyhoop-dev worktree remove /root/mh-t184 --force
git -C /root/milkyhoop-dev branch -D feat/t184-kartu-jurnal
```

## Nol perubahan DB
Tiket ini NOL migrasi, NOL DDL, NOL tulisan DB. Rollback = kode saja.
