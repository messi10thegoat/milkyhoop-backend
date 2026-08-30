# T181 FASE 1 — Daftar probe untuk OWNER (dijalankan SESUDAH deploy)

Branch: feat/t181-tolak (kode: 8bfc168d) · baseline master a0179147
Tenant probe: **kaos-biru-konveksi SAJA**. NOL tulisan ke grapgrap-manado.
BERHENTI di kartu konfirmasi. JANGAN terbitkan jurnal.
JANGAN SENTUH: BRG-0003 · BRG-0004 · QUO-2608-0002 · 0010 · 0011 · 0013 ·
SO-2608-0010 · pending_action 923fc93b.

Maksimal 2 ulangan per string; rasio dihitung PER STRING (model
non-deterministik: terukur 22 vs 10 pada prompt identik).

## Isi master saat baseline (SQL, 2026-08-30) — dipakai memilih stimulus
- produk aktif (deleted_at IS NULL): **Kaos Hitam 24s** (jual 55.000 / beli
  35.000, pcs) · **Kaos Biru 30s** (50.000 / 35.000, pcs). Hanya DUA.
- vendor: **PT Grosir Kaos** (satu-satunya). "Knitto Textile" TIDAK ADA di
  master — tapi kartu bill tetap terbit untuknya (nama vendor diusulkan),
  jadi ia sah sebagai stimulus.
- customer: **Toko Merdeka**, **Toko Melati**.
- Karena hanya ada DUA produk, kontrol "3 item" TIDAK BISA seluruhnya
  memakai barang master. Itu diterima untuk bill (baris diusulkan per nama),
  tapi untuk sisi jual pakai dua barang yang ADA.

## Yang harus dikutip untuk SETIAP probe
1. `[EXTRACT_S2] n_items= tipe=`
2. `[T181_TOLAK] action= len=`  (ADA / TIDAK ADA — dan pastikan TANPA isi string)
3. `[MERGE_ITEMS] pre= post=`
4. `[T168_PICU] baris=`
5. jumlah baris `action_plan`
6. **teks yang dilihat user** (salin apa adanya dari layar)

Perintah log:
    docker logs --since 3m milkyhoop-dev-api_gateway 2>&1 | grep -E "EXTRACT_S2|T181_TOLAK|MERGE_ITEMS|T168_PICU"

## P0 — MERAH, harus BERSUARA
    Catat faktur pembelian dari vendor Knitto Textile, 2 item: Kain Katun 10 meter @ 40000, Benang Jahit 5 pcs @ 50000, jatuh tempo 30 hari
LULUS bila: TIDAK ada kartu satu baris ber-`Item`; user melihat pesan
"Daftar barang di pesan ini tidak bisa saya urai…" yang MENGUTIP teksnya;
`[T181_TOLAK]` terbit TEPAT SEKALI, hanya `action=` + `len=`.
GAGAL bila: kartu tetap terbit, ATAU log memuat isi stringnya, ATAU
`[T181_TOLAK]` terbit dua kali.

## Kontrol sehat — WAJIB HIJAU (kartu BENAR-BENAR terbit)
- K1 (kalimat asli TANPA "2 item:", terbukti hijau 2/2):
      Catat faktur pembelian dari vendor Knitto Textile, Kain Katun 10 meter @ 40000, Benang Jahit 5 pcs @ 50000, jatuh tempo 30 hari
- K2 (kalimat asli dengan "3 item:", terbukti hijau 3/3):
      Catat faktur pembelian dari vendor Knitto Textile, 3 item: Kain Katun 10 meter @ 40000, Benang Jahit 5 pcs @ 50000, Kaos Hitam 24s 3 pcs @ 60000, jatuh tempo 30 hari
- K3 (bill dua baris, barang MASTER):
      Catat faktur pembelian dari PT Grosir Kaos, Kaos Hitam 24s 10 pcs @ 35000 dan Kaos Biru 30s 5 pcs @ 35000, jatuh tempo 30 hari
- **K4 (bill SATU barang — kasus (A), jalur mengarang yang SAH):**
      Catat faktur pembelian dari PT Grosir Kaos, Kaos Biru 30s 5 pcs @ 35000
  Kalau K4 rusak, patch-nya SALAH -> ROLLBACK LANGSUNG.
- K5 (faktur penjualan dua baris — situs kedua):
      Buat faktur penjualan untuk Toko Merdeka, Kaos Hitam 24s 4 pcs @ 55000 dan Kaos Biru 30s 6 pcs @ 50000
- K6 (penawaran dua baris — situs ketiga):
      Buat penawaran untuk Toko Melati, Kaos Hitam 24s 4 pcs @ 55000 dan Kaos Biru 30s 6 pcs @ 50000
- K7 (pesanan penjualan dua baris — situs keempat):
      Buat pesanan penjualan untuk Toko Merdeka, Kaos Hitam 24s 4 pcs @ 55000 dan Kaos Biru 30s 6 pcs @ 50000

Setiap kontrol yang menghasilkan **NOL kartu** (bot bertanya balik) dihitung
**GAGAL SEBAGAI KONTROL**, bukan hijau — ganti stimulusnya, jangan dicatat
sebagai cakupan.

## Hitungan entitas — SEBELUM dan SESUDAH seluruh probe (harus IDENTIK)
Lingkup: kolom `kaos` = tenant kaos-biru-konveksi; `total` = GLOBAL.
Baseline 2026-08-30:
    bills            kaos=1   total=4
    journal_entries  kaos=11  total=27
    products         kaos=54  total=63   (aktif/deleted_at IS NULL = 2)
    customers        kaos=2   total=15
    vendors          kaos=1   total=2

SQL:
    docker exec milkyhoop-dev-postgres-1 psql -U postgres -d milkydb -c "select 'bills' t, count(*) filter (where tenant_id='kaos-biru-konveksi') kaos, count(*) total from bills union all select 'journal_entries', count(*) filter (where tenant_id='kaos-biru-konveksi'), count(*) from journal_entries union all select 'products', count(*) filter (where tenant_id='kaos-biru-konveksi'), count(*) from products union all select 'customers', count(*) filter (where tenant_id='kaos-biru-konveksi'), count(*) from customers union all select 'vendors', count(*) filter (where tenant_id='kaos-biru-konveksi'), count(*) from vendors"

## Perintah MERGE + RESTART (owner)
    git -C /root/milkyhoop-dev rev-parse master        # HARUS a0179147...
    docker inspect -f "{{.State.StartedAt}}" milkyhoop-dev-api_gateway   # catat SEBELUM
    git -C /root/milkyhoop-dev merge --ff-only feat/t181-tolak
    git -C /root/milkyhoop-dev rev-parse master        # HARUS = git -C /root/mh-t181c rev-parse feat/t181-tolak
    docker compose -f /root/milkyhoop-dev/docker-compose.yml up -d api_gateway
    docker inspect -f "{{.State.StartedAt}}" milkyhoop-dev-api_gateway   # HARUS BERGESER

Bukti deploy yang diterima HANYA: pergeseran `StartedAt` + perbedaan perilaku
lewat milkyhoop.com. BUKAN md5 (hanya membuktikan merge lewat bind-mount),
BUKAN `inspect.getsource` via `docker exec python -c` (proses baru, hijau
walau restart gagal).

## ROLLBACK — lihat DOCS/T181-FASE1-ROLLBACK.md
    git -C /root/milkyhoop-dev reset --keep a0179147
    docker compose -f /root/milkyhoop-dev/docker-compose.yml up -d api_gateway
JANGAN `--hard` (tree kotor 75 entri milik orang lain).
**Kalau SATU SAJA kontrol sehat rusak: ROLLBACK LANGSUNG, baru diagnosis.**

## ADDENDUM OWNER 2026-08-30 — gerbang diperluas, gate offline diperkuat
Rumusan gerbang BUKAN lagi "exception terjadi", melainkan:
**"ADA teks daftar mentah, DAN kita berakhir TANPA baris."**
Satu fungsi murni tingkat-modul `_t181_urai_items(payload)` dipakai KEEMPAT
situs (menggantikan empat salinan try/except). Gate offline:

    python3 /root/mh-t181c/scripts/gate_t181_fase1.py

memanggil fungsi itu APA ADANYA dari berkas (ast + exec), dengan payload yang
disusun sendiri — deterministik, offline, nol produksi.

Keluaran terukur:
    B1 JSONDecodeError       -> TOLAK  n_baris=0  sentinel=ADA
    B2 sukses tapi dict      -> TOLAK  n_baris=0  sentinel=ADA
    B3 teks biasa            -> TOLAK  n_baris=0  sentinel=ADA
    B4 daftar kosong '[]'    -> TOLAK  n_baris=0  sentinel=ADA
    B5 daftar sah 2 baris    -> BARIS  n_baris=2  sentinel=tidak
    A  items tidak ada       -> KARANG n_baris=1  sentinel=tidak
    A2 items=[] (list asli)  -> KARANG

### KEPUTUSAN OWNER YANG MASIH TERBUKA: `items = '[]'`
Rumusan gerbang owner ("ada teks mentah, berakhir tanpa baris") membuat teks
`'[]'` **DITOLAK**. Ini DILAPORKAN, bukan diputuskan agen. Kalau owner menilai
daftar kosong eksplisit itu SAH, perubahannya satu baris di `_t181_urai_items`:
kecualikan hasil parse berupa list kosong. Sampai owner memutuskan: TOLAK.

### Probe hidup
Tidak ada stimulus alami yang diketahui menghasilkan `items='[]'` atau dict;
keduanya sudah tercakup deterministik oleh gate offline di atas. Daftar probe
hidup P0/K1-K7 TIDAK berubah. **K4 (bill SATU barang: PT Grosir Kaos +
Kaos Biru 30s) tetap kontrol paling menentukan** — ia satu-satunya yang menguji
kasus (A), jalur mengarang yang SAH. Rusak = ROLLBACK LANGSUNG.
