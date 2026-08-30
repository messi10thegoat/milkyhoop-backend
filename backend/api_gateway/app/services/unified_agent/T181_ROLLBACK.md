# T181 LANGKAH 2 — RENCANA ROLLBACK (ditulis SEBELUM berkas kode disentuh)

Cabang: `feat/t181-puing` dari master `07bb28ee`.
Isi: SATU sisipan `logger.warning` di `entity_extractor.py`, penanda `[T181_PUING]`.
Sifat: LOG-ONLY. Nol perubahan nilai kembalian, nol perubahan perilaku.

## Rollback (kalau apa pun mencurigakan)
    git -C /root/milkyhoop-dev checkout master
    git -C /root/milkyhoop-dev reset --keep 07bb28ee
    docker restart milkyhoop-dev-api_gateway
    docker inspect -f '{{.State.StartedAt}}' milkyhoop-dev-api_gateway   # harus BERGESER
    docker logs milkyhoop-dev-api_gateway --since 5m 2>&1 | grep -c T181_PUING   # harus 0

JANGAN `git reset --hard` — pohon utama punya 75 entri kotor milik pihak lain
(`frontend/` + 2 `.env.bak` + `frontend/BUILD_INFO.json`). `--keep` menjaganya.

## ⚠️ PENANDA INI WAJIB DICABUT
`[T181_PUING]` mencetak STRING MENTAH keluaran model, yang berisi NAMA BARANG,
JUMLAH, dan HARGA yang diketik pengguna — yaitu DATA USER.
Aman untuk ronde ini HANYA karena probe dijalankan di tenant uji
`kaos-biru-konveksi`. Sebelum dibiarkan jalan lama di tenant nyata
(mis. `grapgrap-manado`), penanda ini WAJIB DICABUT atau diturunkan menjadi
hash/panjang saja. Batas praktis: cabut segera setelah probe T181 selesai
dibaca; jangan tinggalkan semalaman.

## Bukti deploy yang diterima
HANYA: pergeseran `StartedAt` + `[T181_PUING]` terbit di log yang sebelumnya nol.
BUKAN md5 berkas (hanya membuktikan merge lewat bind-mount).
BUKAN `inspect.getsource` via `docker exec python -c` (proses baru; hijau walau restart gagal).
