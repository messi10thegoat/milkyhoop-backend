# Rilis kelas IMAGE (api_gateway) — bind-mount + restart TIDAK CUKUP

## Kapan sebuah rilis naik ke kelas IMAGE

Kalau salah satu berkas ini berubah, `mh-restart.sh` **tidak cukup** dan
kontainer akan hidup dengan isi yang lama tanpa satu galat pun:

| Berkas | Yang tidak ikut kalau hanya restart |
|---|---|
| `backend/api_gateway/Dockerfile` | paket apt, font, biner sistem |
| `backend/api_gateway/requirements*.txt` | pustaka Python |
| apa pun yang di-`COPY` saat build (bukan bind-mount) | isinya |

Kode Python dan template PDF **ada di bind-mount**, jadi untuk perubahan itu
`mh-restart.sh` memang cukup. Yang membedakan bukan "besar kecilnya
perubahan", melainkan **apakah artefaknya lahir saat build**.

## Kenapa ini kelas kegagalan yang berbahaya

Kelasnya sama dengan `mh-recreate.sh` untuk frontend: **exit 0, kontainer
`healthy`, konten salah**. Untuk font, akibatnya tak terlihat di log sama
sekali — WeasyPrint tidak pernah mengeluh saat font yang diminta tidak ada;
ia diam-diam memakai penggantinya. Faktur tercetak dengan huruf yang salah
dan yang pertama tahu adalah penerima faktur.

Karena itu rilis kelas image **wajib** ditutup gerbang `scripts/gate_font.py`,
yang diuji DUA ARAH: image baru harus memakai Liberation, image lama harus
memakai DejaVu. Gerbang satu arah tak membuktikan apa pun.

## Urutan

```
# 0. arsip: catat image yang SEDANG jalan supaya ada jalan pulang
docker tag milkyhoop-dev-api_gateway milkyhoop-dev-api_gateway:sebelum-$(date +%Y%m%d)

# 1. merge
cd /root/milkyhoop-dev && git merge --no-ff <cabang> -m "<pesan>"

# 2. BANGUN ULANG (bukan restart) — ±6-9 menit, sebagian besar di apt+pip
docker compose build api_gateway && docker compose up -d api_gateway

# 3. gerbang font DUA ARAH
python3 scripts/gate_font.py milkyhoop-dev-api_gateway milkyhoop-dev-api_gateway:sebelum-<tanggal>

# 4. gerbang rupa terhadap PDF acuan (dijalankan di Mac: butuh sips + PIL)
python3 scripts/gate_rupa.py contoh-template-b.pdf contoh-template-a.pdf
```

Kalau langkah 3 merah, **kembalikan** dengan
`docker tag milkyhoop-dev-api_gateway:sebelum-<tanggal> milkyhoop-dev-api_gateway`
lalu `docker compose up -d api_gateway`.

## Catatan template B

- `.logo` dipatok **tinggi**-nya (14mm), bukan lebarnya. Acuan pemilik memakai
  logo memanjang selebar ~67mm; logo tenant bisa persegi, dan memaksa lebar
  67mm berarti tinggi 67mm yang mendorong seluruh kop. Ini **beda yang
  disengaja**, bukan kelalaian.
- Medan yang belum ada dan sengaja dicetak kosong supaya ketiadaannya
  terlihat: `Tenant.workshop_address`, `Tenant.signatory_name`, cabang bank
  pada faktur.
