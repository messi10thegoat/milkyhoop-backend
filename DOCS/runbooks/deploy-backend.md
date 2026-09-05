# RUNBOOK — Deploy backend (berlaku umum)

Bentuk BAKU, tiga perintah **TERPISAH**. Jangan dirangkai dengan `&&`: kalau
dirangkai, kegagalan di tengah menyisakan keadaan separuh dan keluarannya
bercampur sehingga sulit tahu langkah mana yang jatuh.

```bash
ssh root@<host> 'cd /root/mh-pdf && git push deploy <cabang>:master'
ssh root@<host> 'cd /root/milkyhoop-dev && git pull --ff-only deploy master'
ssh root@<host> '/root/milkyhoop-dev/scripts/ops/mh-restart.sh api_gateway'
```

`--ff-only` disengaja: kalau pohon deploy sudah bergerak, ia MENOLAK alih-alih
membuat commit gabungan diam-diam di pohon yang seharusnya cuma mengikuti.

`mh-restart.sh`, BUKAN `docker restart`: ia mengarsip log lebih dulu (log
forensik pernah hancur dua kali dalam satu hari) dan mencetak StartedAt
sebelum/sesudah supaya restart yang TIDAK terjadi tak bisa menyamar sukses.

## WAJIB sesudah restart — deploy tanpa ini dianggap BELUM SELESAI

```bash
docker run --rm --network host -v /root/milkyhoop-dev:/w \
  -e MH_UJI_EMAIL="$MH_UJI_EMAIL" -e MH_UJI_SANDI="$MH_UJI_SANDI" \
  -w /w milkyhoop-dev-api_gateway python3 scripts/gate_asap.py http://localhost:8001
```

**Kenapa wajib, dengan kejadiannya.** Pada 2026-09-05, deploy `a3931d80`
mematikan `GET /api/sales-invoices/{id}` — **500 untuk SEMUA faktur**, 16
kejadian dalam 25 menit — sementara SELURUH gerbang lain HIJAU (`gate_uang`,
`gate_tpl`, `gate_so_po`) dan `healthz` menjawab 200 sepanjang insiden.

Sebabnya struktural, bukan satu gerbang yang kurang: semua gerbang itu bekerja
di lapis **LAYANAN** — memanggil fungsi render, skema, kueri — dan tak satu pun
menembak **RUTE**-nya. Lapis itu tidak bisa melihat rute mati. `py_compile`
juga tidak menangkap `NameError` di cabang yang tidak dieksekusi.

`healthz` tidak menggantikan ini. Ia hidup sepanjang insiden.

## Memulihkan: patokannya StartedAt, bukan jendela waktu

```bash
S=$(docker inspect milkyhoop-dev-api_gateway --format '{{.State.StartedAt}}')
docker logs --since "$S" milkyhoop-dev-api_gateway 2>&1 | grep -c '<pola galat>'
```

`docker logs --since 12m` mencakup periode PRA-restart dan akan tetap
menunjukkan galat lama; itu bukan bukti bahwa perbaikannya gagal. Hitung sejak
`StartedAt`.

## Migrasi

Nomor V dipesan dengan **fetch-before-apply**: `git fetch deploy` lalu periksa
`git ls-tree deploy/master -- backend/migrations` DAN `schema_migrations` di
basis data. Migrasi yang SUDAH diterapkan **tidak boleh diedit** — checksum
tercatatnya berhenti cocok dan yang menolaknya nanti adalah runner
fresh-install, yakni jalur pemulihan. Selalu sertakan berkas `_ROLLBACK.sql`.

## Kelas IMAGE

Perubahan `Dockerfile` atau `requirements*.txt` menuntut `docker compose build`
— bind-mount + restart TIDAK cukup. Lihat `rilis-kelas-image.md`.
