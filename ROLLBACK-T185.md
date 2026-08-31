# ROLLBACK T185 — amplop gerbang sampai ke layar

Ditulis SEBELUM kode disentuh. Branch `feat/t185-amplop-gerbang`,
worktree `/root/mh-t185`, dicabang dari master `6324d2e6`.

## Keadaan yang dijaga
- `git -C /root/milkyhoop-dev rev-parse master` HARUS `6324d2e6...`
- Batch ini TIDAK di-merge, TIDAK di-deploy, TIDAK menyentuh FE, NOL tulisan DB.
- Selama tidak di-merge, produksi TIDAK terpengaruh sama sekali: kontainer
  membaca /root/milkyhoop-dev, bukan /root/mh-t185.

## Commit batch ini (diisi saat dibuat; SEMUA disebut)
- T185-1  kontrak render + tes penegak (berkas BARU, nol perubahan perilaku)
- T185-2  perbaikan penyaring CLARIFICATION di `_to_chat_response`
          (`app/routers/unified_chat.py`) — SATU cabang elif.

## Berkas yang disentuh
- BARU  backend/api_gateway/app/services/unified_agent/kontrak_render.py
- BARU  backend/api_gateway/tests/unit/test_kontrak_render.py
- UBAH  backend/api_gateway/app/routers/unified_chat.py  (cabang elif CLARIFICATION)
- BARU  ROLLBACK-T185.md (berkas ini)
- TIDAK disentuh: gerbang_entitas.py, tool_executor.py, orchestrator.py, FE.

## Kalau batch ini terlanjur ter-merge dan produksi rusak
Langkah darurat, PERSIS ini, tidak ada varian lain:

    git -C /root/milkyhoop-dev reset --keep 6324d2e6
    docker restart milkyhoop-dev-api_gateway

JANGAN `docker compose up --force-recreate` (me-recreate redis/gateway =
kelas kegagalan lain). JANGAN `git reset --hard` (75 entri kotor di
/root/milkyhoop-dev BUKAN milik batch ini dan akan hilang). `--keep`
dipilih justru supaya perubahan tak-ter-commit itu selamat.

## Membuang worktree ini tanpa menyentuh master
    git -C /root/milkyhoop-dev worktree remove --force /root/mh-t185
    git -C /root/milkyhoop-dev branch -D feat/t185-amplop-gerbang

## Radius kerusakan bila patch T185-2 salah
Cabang yang diubah HANYA dijalankan saat `message_type == "CLARIFICATION"`.
Yang berubah: `data` yang tadinya dipaksa `None` saat `options` kosong kini
diteruskan bila memuat `question`. Kombinasi yang SUDAH bekerja hari ini
(CLARIFICATION + options berisi = pil entitas) melewati cabang yang sama dan
diuji tetap lolos. Tidak ada message_type lain yang menyentuh cabang ini.
