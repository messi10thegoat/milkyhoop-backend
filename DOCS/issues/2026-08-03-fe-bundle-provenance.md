# TICKET: live FE bundle has NO git provenance — exists only inside the running image

**Date:** 2026-08-03  **Severity:** HIGH (live UI is unreproducible if the image is lost)
**Status:** OPEN — blocks any UI walkthrough that must reflect current source. Fix = rebuild from a
pinned source commit (runbook DOCS/runbooks/2026-07-27-batch2-deploy-runbook.md, FE section).

## Facts
- Live FE = container `milkyhoop-dev-frontend-1`, serving bundle **main.5558c404.js**.
- The server FE build tree `/root/milkyhoop-dev/frontend` is a git dir whose HEAD build is
  **main.6a02fcc0.js**; `git ls-files --deleted` shows the committed 6a02fcc0 assets are MISSING
  from the working tree, which instead holds 5558c404 (swapped in manually, uncommitted).
- **NEW FACT (2026-08-03):** `milkyhoop-dev-frontend-1` has **ZERO bind mounts** — assets are
  BAKED INTO THE IMAGE at build time. Therefore the live bundle **5558c404 exists ONLY inside the
  running container image**. It is in NO git tree (HEAD=6a02fcc0, working=deleted/swapped) and in NO
  committed build. **If that image is lost/rebuilt, the currently-running UI cannot be reproduced.**
- FE SOURCE (Mac /Users/antoniwan/milkyhoop/frontend/web) is clean + complete at 2bd845159, node
  v18.20.8, builds via react-scripts, `.env.local REACT_APP_API_URL=` empty (relative), no
  `.env.production`.

## Why this matters
A UI walkthrough on the live 5558c404 proves nothing about current source (unknown provenance), and
the source-of-truth for what users see is a mutable container image, not git. This strengthens the
rebuild-from-pinned-source plan: build FE from a pinned commit on the Mac → deploy fresh assets →
verify hash ≠ 5558c404 → walkthrough. Do NOT `git restore` the server tree (brings back the stale
6a02fcc0, still not a fresh build). Do NOT rebuild the frontend image before capturing/deciding on
5558c404, or the running UI becomes unreproducible.

## Related
Backend deploy #2 (2026-08-03, A1/B1/credit_notes) deliberately did NOT touch the frontend
container or the frontend/ tree (backend-only, ff-only merge not reset --hard) precisely to avoid
disturbing this state.

---

# ADDENDUM (2026-08-03, sesi rebuild) — KOREKSI + diagnosis yang lebih tajam

## KOREKSI: klaim "exists ONLY inside the running container image" SALAH

Bukti `[CODE]`:
- `/root/milkyhoop-dev/frontend/static/js/main.5558c404.js` **ADA di host build tree**
  (tanggal 24 Jul 08:36; image dibuild 08:38).
- md5 host = md5 dalam container = `0fe622a6813ffa793efe9bc6584faadf` — **byte-identik**.
- Dia tidak muncul di `git status` **bukan karena hilang**, tapi karena di-ignore:
  `.gitignore:84 → frontend/static/`. `git status --porcelain frontend/` = 70 `D` + 2 `M` + **nol `??`**.

Konsekuensi: **rollback punya 2 jalur, bukan 1.**
1. `docker load < /root/fe-image-5558c404.tar.gz` + restart container FE.
2. Extract `/root/fe-hosttree-5558c404.tar.gz` ke `/root/milkyhoop-dev/frontend/`
   + `docker compose build frontend && docker compose up -d frontend`.
   (Jalur ini lebih kecil — 6.8 MB vs 39.7 MB — dan kemungkinan besar yang dipakai.)

Arsip A0 (2026-08-03), sha256 tercatat, tersalin ke
`~/Dropbox/MILKYHOOP/RECOVERY_2026-07-23/fe-archive/`:
```
378180612b1917f339198c663fd5320a2f3947ad230ce1c26acf41e2fbeafa44  fe-image-5558c404.tar.gz     (39,673,009 B)
2ef29efe19f78613801b95105b3b78c0b36be67df19e8daae4cb762ad4ba3f8b  fe-hosttree-5558c404.tar.gz  ( 6,799,798 B)
```
Arsip diverifikasi **bisa direstore**, bukan sekadar "file ada": bundle di-extract dari tar
di Mac → md5 = `0fe622a6...` (cocok live). Kontrol negatif: salinan yang dimutasi 1 baris
→ md5 `ba8f83ce...` (berbeda) — jadi pengecekannya terbukti BISA gagal.

## DIAGNOSIS SEBENARNYA: ini keputusan desain, bukan kecelakaan

Provenance nol **bukan** karena artefak hilang. Penyebabnya struktural:

- `frontend/static/` **di-gitignore** (build output sengaja tak masuk git — benar, itu praktik standar:
  bundle = artefak turunan, bukan sumber; meng-commit-nya membengkakkan repo dan bikin diff berisik).
- Tapi `frontend/` (pembungkus image: `Dockerfile`, `nginx.conf`, `index.html`,
  `asset-manifest.json`) **ter-track**.

Jadi git melacak *pembungkusnya* tapi bukan *isinya*, dan **tidak ada satu pun tempat yang mencatat
SHA sumber yang menghasilkan isi itu.** Build jadi tak-terlacak by construction, bukan karena lupa.

**Maka fix-nya BUKAN meng-commit bundle.** Meng-commit build output akan membatalkan keputusan
desain yang benar dan cuma memindahkan masalahnya. Yang hilang adalah **metadata**, bukan artefak.

## USULAN MEKANISME (usul saja — JANGAN diimplement tanpa GO owner)

Yang paling murah, urut dari paling disarankan:

**Opsi 1 — `BUILD_INFO.json` yang ikut ter-deploy (REKOMENDASI).**
Satu file kecil di root build, ikut ter-`COPY` oleh Dockerfile yang sudah ada
(`COPY . /usr/share/nginx/html/` — nol perubahan Dockerfile), jadi bisa dicek dari luar:
`curl -s https://milkyhoop.com/BUILD_INFO.json`.
```json
{ "source_sha": "2bd845159", "source_branch": "feat/kontak-uangmuka-align",
  "built_at": "2026-08-03T05:12:00Z", "main_bundle": "main.<hash>.js", "tree_clean": true }
```
Dihasilkan satu baris di skrip build (`git rev-parse HEAD` + `git status --porcelain | wc -l`).
Kelebihan: **terverifikasi dari edge**, tak tergantung akses server, dan menjawab persis pertanyaan
"bundle yang sedang dilihat user ini dari commit mana". `tree_clean:false` langsung menandai
build dari working tree kotor.

**Opsi 2 — sisipkan ke `asset-manifest.json`.**
Lebih sedikit file, tapi manifest itu milik CRA/Workbox — menambah key di situ berisiko kena
timpa saat regenerasi dan mencampur milik-kita dengan milik-tool. Kurang disarankan.

**Opsi 3 — label OCI di image** (`--label org.opencontainers.image.revision=<sha>`).
Bagus sebagai *pelengkap* (bertahan di image walau file terhapus), tapi **tak terlihat dari edge** —
harus SSH + `docker inspect`. Jadikan tambahan Opsi 1, bukan pengganti.

**Gate yang mengikat:** apa pun yang dipilih, deploy FE tidak dianggap selesai sampai
`curl <edge>/BUILD_INFO.json` mengembalikan sha yang **sama dengan** commit yang di-pin.
Gate yang tak pernah bisa gagal = gate palsu — jadi gate ini harus diuji dengan sengaja
men-deploy sha yang salah sekali, dan memastikan ia MERAH.

## Status tiket
Masih **OPEN**. Rebuild-from-pinned-source (pin = `2bd845159`) menutup gap untuk build BERIKUTNYA;
mekanisme BUILD_INFO di atas yang mencegahnya terulang. Bundle `5558c404` sendiri
**tetap tak ber-provenance selamanya** — sumbernya tak dapat dipulihkan; dia sekarang
sekadar diarsipkan untuk rollback.

---

# ADDENDUM 2 (2026-08-04) — TIKET DITUTUP untuk build berikutnya: rebuild dari pin BERHASIL

## Yang dikerjakan
Rebuild FE dari commit yang di-pin **`2bd845159264cfacbad3fac00a2a625a40fe22ff`**
(branch `feat/kontak-uangmuka-align`, BELUM di-merge ke master — lihat catatan kejujuran di bawah),
lalu deploy. Build **wajib di Mac**: server tak bisa build FE (`frontend/Dockerfile` = `FROM
nginx:alpine` + `COPY . /usr/share/nginx/html/` — hanya membungkus asset jadi; `/root/milkyhoop-dev/
frontend/web` tidak ada).

## Hasil `[HTTP]` `[CODE]`
- Bundle baru: **`main.8f12c2eb.js`** (≠ `5558c404` ✅)
- Mac tree BERSIH di pin: `git status --porcelain -- frontend/web` = 0 baris
- `npm ci` (bukan `npm install`) dari lockfile, node v18.20.8, CRA 5.0.1
- rsync: **302 file dihapus** (seluruh `static/` generasi lama: 301 js + 1 css),
  **660 file dikirim** (= seluruh isi `build/`), **nol** wrapper tersentuh
  (`Dockerfile`/`nginx.conf`/`50x.html` ter-exclude, terverifikasi)
- `docker compose build frontend && docker compose up -d frontend` (NAMED service).
  `api_gateway` StartedAt tetap `2026-08-02T18:26:55Z` — tidak tersentuh ✅

## Verifikasi tiga titik — COCOK BERTIGA
| Titik | Hasil |
|---|---|
| (a) dalam container | `/usr/share/nginx/html/static/js/main.8f12c2eb.js` |
| (b) origin langsung `127.0.0.1:3001` (bypass Cloudflare) | HTTP 200, index.html → `main.8f12c2eb.js` |
| (c) edge `milkyhoop.com` | HTTP 200, index.html → `main.8f12c2eb.js` |

`curl https://milkyhoop.com/BUILD_INFO.json` → `source_sha` = `2bd8451592…` ✅
Halaman utama 200; login `POST /api/auth/login` → **HTTP 200**, access_token terbit,
`tenant_id=kaos-biru-konveksi` ✅

**TEMUAN: purge Cloudflare TIDAK diperlukan.** `index.html` dan `BUILD_INFO.json` keduanya
`cf-cache-status: DYNAMIC` — Cloudflare tak meng-cache-nya, jadi edge langsung menyajikan yang baru
tanpa intervensi. Aset lain ber-hash-konten (nama file berubah tiap build) sehingga tak pernah basi
by construction. Bundle lama `main.5558c404.js` masih 200 di edge (`cf-cache-status: HIT`) padahal
**origin sudah 404** — itu murni sisa cache CF atas URL yang tak lagi dirujuk siapa pun; tak berbahaya.

## Mekanisme BUILD_INFO: Opsi 1 DIIMPLEMENT
Sesuai usul di Addendum 1, `BUILD_INFO.json` ditaruh di root `build/` sebelum rsync. Nol perubahan
Dockerfile (`COPY . /usr/share/nginx/html/` otomatis membawanya). Isi yang ter-deploy:
```json
{
  "source_sha": "2bd845159264cfacbad3fac00a2a625a40fe22ff",
  "source_branch": "feat/kontak-uangmuka-align",
  "built_at": "2026-08-04T08:55:19Z",
  "main_bundle": "main.8f12c2eb.js",
  "tree_clean": true,
  "built_by": "antoniwan@Mac (claude-code session)"
}
```
Field `source_branch` ditambahkan atas permintaan owner, dan itu tepat: build ini berasal dari
**branch fitur yang belum di-merge**, bukan dari trunk. Tanpa field itu pembaca BUILD_INFO akan
mengira ini rilis dari master. Provenance harus jujur soal itu.

## UTANG YANG MASIH TERBUKA
1. **Uji-merah gate BUILD_INFO belum dijalankan.** Addendum 1 sendiri menuntut: "gate ini harus
   diuji dengan sengaja men-deploy sha yang salah sekali, dan memastikan ia MERAH." Itu ditunda atas
   keputusan owner (jangan saat deploy pertama). **Sampai uji-merah itu dijalankan, gate BUILD_INFO
   belum boleh dipercaya** — lihat draft Iron Law 33 (`DOCS/proposals/2026-08-04-iron-law-33-*.md`),
   yang justru lahir dari sesi ini.
2. Bundle `5558c404` tetap tak ber-provenance selamanya (sumbernya tak dapat dipulihkan); dia kini
   hanya artefak rollback.
3. `source_branch` menunjuk branch belum-merge → begitu `2bd845159` di-merge ke master,
   build berikutnya sebaiknya dari master supaya provenance menunjuk trunk.

## Rollback (siap pakai, tidak terpakai)
`/root/fe-rollback.sh` (executable, `bash -n` lulus, nol placeholder) ditulis **sebelum** deploy.
Memverifikasi sha256 arsip → extract ke staging → **guard: batal kalau staging tak lengkap, sehingga
`--delete` tak pernah menyentuh target dari extract yang gagal** → rsync balik → rebuild image.
Top-dir tar = `./` (nol direktori pembungkus). Tar host-tree **berisi** `Dockerfile`/`nginx.conf`/
`50x.html`, jadi rollback benar **tanpa** exclude — kebalikan dari forward path, karena rollback
memulihkan seluruh tree, bukan menyuntikkan build CRA ke dalamnya.

⚠️ `--delete` sudah menyapu bundle lama dari host tree. Satu-satunya salinan `5558c404` kini ada di
**arsip** (`/root/fe-hosttree-*.tar.gz`, `/root/fe-image-*.tar.gz` + mirror Dropbox) dan **image lama**.
JANGAN hapus keduanya.

## Status tiket
**CLOSED** untuk gap yang dijelaskan: build live kini punya provenance yang terverifikasi dari edge.
Yang tersisa = utang no.1 (uji-merah gate) yang dilacak di draft Iron Law 33.
