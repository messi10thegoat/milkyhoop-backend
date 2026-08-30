# Suite unit — uji fungsi murni, nol HTTP / LLM / DB

## Jalankan
```bash
scripts/jalankan_unit.sh /root/mh-harness          # seluruh suite (~0,3 detik)
scripts/jalankan_unit.sh /root/mh-harness -k skema # sebagian
```
Berjalan di kontainer **sekali-pakai** dari image `milkyhoop-dev-api_gateway:latest`.
Kontainer produksi tidak disentuh dan tidak dipasangi apa pun.

## Kenapa ada
Sebelum ini, pertanyaan seperti *"apakah baris kedua hilang?"* hanya bisa dijawab
lewat HTTP → LLM → DB: ~10 menit per hipotesis, dan karena modelnya
non-deterministik tiap hipotesis butuh belasan probe. Empat ronde perbaikan
T181 menambal jalur yang salah karena mahalnya menjawab pertanyaan dasar.

Dengan `FakeLLM`, pertanyaan yang sama dijawab **deterministik dalam hitungan
detik**.

## Batas — jangan diklaim lebih
- Suite ini menguji **kode kita saat model mengembalikan bentuk X**.
- Ia **tidak** membuktikan model sungguh mengembalikan bentuk X.
- **Kalau harness dan produksi berbeda, PRODUKSI MENANG.** Terukur 2026-08-30:
  harness dengan `collected={}` melaporkan "items tidak ada 18/18" sementara
  produksi 4/4 mengirim string yang gagal parse. Harness yang dimenangkan saat
  itu memakan satu ronde penuh.
- Probe produksi tetap wajib untuk perubahan yang menyentuh perilaku di layar.
  Gate yang berhenti di nilai kembalian fungsi sudah pernah meloloskan
  `text: null` ke layar pengguna (T181 Fase 1, di-rollback).

## Aturan menulis tes di sini
1. **Setiap NOL wajib kontrol positif.** Pakai fixture `kontrol_fake_llm` untuk
   membuktikan LLM palsu benar-benar dipanggil — kalau tidak, nolnya berarti
   "tak pernah terpicu", bukan "tak pernah terjadi".
2. **Setiap penjaga wajib punya pasangan yang membuktikan ia bisa GAGAL.**
   Contoh: `test_clean_schema_memang_meruntuhkan_union` menjaga
   `test_array_lolos_clean_schema_tanpa_diruntuhkan`.
3. **Nyatakan amplop yang dibaca.** `build_intent_schema` mengembalikan
   `{"json_schema":{"schema":{"properties":…}}}`, bukan `properties` di tingkat
   atas. Membaca amplop yang salah menghasilkan nol yang meyakinkan — itu
   terjadi saat menulis suite ini, dan sudah empat kali terjadi di proyek ini
   (payload Bill `product_name`/`price` vs Quote `description`/`unit_price` vs
   review_card `name`/`price`; amplop `items` vs `data`).
4. **Suite ini harus MERAH pada kode yang salah.** Cara membuktikannya:
   ```bash
   git worktree add /root/mh-buktimerah --detach <SHA-sebelum-perbaikan>
   cp -r tests/unit pytest-unit.ini /root/mh-buktimerah/backend/api_gateway/
   scripts/jalankan_unit.sh /root/mh-buktimerah    # harus ada yang GAGAL
   ```
   Diverifikasi 2026-08-30: 3 merah di `a0179147`, 14 hijau di `1ac66806`.

## Kenapa `pytest-unit.ini` terpisah dari `pytest.ini`
`pytest.ini` memaksa `--cov` pada modul auth dengan `--cov-fail-under=40`. Itu
masuk akal untuk suite auth, tapi membuat tiap tes unit menuntut `pytest-cov`
dan gagal karena ambang cakupan yang tak ada hubungannya. Memisahkan =
nol perubahan pada suite yang sudah ada.

## Catatan tentang suite lama
`tests/chat/conftest.py` menembak **produksi** lewat HTTPS dengan kredensial
tenant `grapgrap` — tenant itu **sudah tidak ada** sejak pemulihan Juli. Suite
chat lama karena itu tidak bisa lulus apa pun. Belum disentuh; ia tiket sendiri.
