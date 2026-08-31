# ROLLBACK — GERBANG ENTITAS FASE 1a

Branch: `feat/gerbang-entitas` (dari `master` = e6b44a34).

## Commit yang dibuat batch ini (SEMUA)
- 57c53be1b5829258c4eaca945b485063bfb8ec62 — gerbang entitas create_bill + gate unit
- c26a656c — docs(rollback): isi SHA commit gerbang entitas
- (commit ini) — betulkan langkah rollback: reset --keep + docker restart

## Cara membatalkan

### Kalau BELUM di-merge
```
git -C /root/milkyhoop-dev worktree remove --force /root/mh-gerbang
git -C /root/milkyhoop-dev branch -D feat/gerbang-entitas
```

### Kalau SUDAH ter-merge dan sudah live  ← JALUR YANG DIPAKAI SAAT DARURAT
```
git -C /root/milkyhoop-dev reset --keep e6b44a34
docker restart milkyhoop-dev-api_gateway
docker inspect -f '{{.State.StartedAt}}' milkyhoop-dev-api_gateway   # harus BERGESER
```

⚠️ `--keep`, BUKAN `--hard`: tree utama memuat 75 entri kotor (frontend/ berisi
bundle live + 2 .env.bak) yang BUKAN milik batch ini. `--hard` akan menghapusnya.

⚠️ `docker restart`, BUKAN `docker compose up`. Gateway disajikan lewat
bind-mount, jadi `restart` sudah cukup; `compose up` bisa me-recreate kontainer
dan memuat ulang `.env` — efek samping yang tidak diminta saat darurat.
(`compose up -d api_gateway` HANYA kalau `.env` sendiri yang berubah — di batch
ini `.env` tidak disentuh sama sekali.)

Bukti rollback berhasil = pergeseran StartedAt + perilaku differential
(kartu create_bill kembali lahir walau vendor belum terdaftar).
BUKAN md5 (bind-mount, cuma membuktikan merge).

Tidak ada migrasi DB, tidak ada tulisan DB, tidak ada perubahan skema.

## Berkas yang disentuh
- `backend/api_gateway/app/services/unified_agent/gerbang_entitas.py` (BARU)
- `backend/api_gateway/app/services/unified_agent/tool_executor.py` (1 sisipan)
- `backend/api_gateway/app/services/unified_agent/orchestrator.py` (cabang CLARIFICATION)
- `backend/api_gateway/tests/unit/test_gerbang_entitas.py` (BARU)
