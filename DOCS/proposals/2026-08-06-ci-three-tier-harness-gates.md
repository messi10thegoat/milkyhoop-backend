# USULAN: tiga tingkat gate CI untuk harness DP-flow

**Status:** USULAN — jangan implement tanpa GO. Disetujui arahnya 2026-08-06.

Konteks: `run_all.sh` kini menjalankan PREFLIGHT nyata (restore + migrate apply + gate skema),
sehingga durasi naik **37s → 114s**. Kenaikan itu jujur — sebelumnya prasyaratnya memang tak
dijalankan. Tapi 114s kurang proporsional sebagai gate per-PR.

| Tingkat | Cakupan | Durasi | Alasan |
|---|---|---|---|
| **Per-PR** | `SKIP_PREFLIGHT=1` + subset step (−1, 0, 0b, 4, 5) + `migrate.sh verify` mandiri | ~30–40s | cukup membuktikan flow tak patah; cepat agar tak menghambat iterasi |
| **Pre-merge ke master** | penuh, dengan PREFLIGHT | ~114s | yang mahal (restore+apply) justru WAJIB sebelum sesuatu masuk trunk |
| **Deploy** | penuh + closing invariant + `check_build_info.sh` | ~120s | gate terakhir sebelum pengguna |

## Kenapa pembagian ini

Yang mahal adalah **preflight**, dan preflight itulah yang menguji **rantai migrasi** — properti yang
hanya relevan saat sesuatu hendak masuk master atau produksi. Per-PR cukup menjawab "apakah alur
bisnisnya masih jalan".

`SKIP_PREFLIGHT=1` sudah mencetak peringatan bahwa **hasilnya tidak sah sebagai verdict**, jadi tak
bisa disalahartikan sebagai bukti penuh.

## Syarat
- Per-PR TIDAK BOLEH jadi satu-satunya gate sebelum merge — pre-merge penuh tetap wajib.
- Kalau ada migration baru dalam PR, PR itu **naik** ke gate penuh (deteksi: `git diff --name-only`
  menyentuh `backend/migrations/`).
