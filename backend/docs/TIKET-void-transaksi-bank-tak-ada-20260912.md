# TIKET — tak ada cara mem-void/menerbitkan SATU transaksi bank (12 Sep 2026)

**Status:** TERBUKA, atas putusan pemilik: **tiketkan dulu, jangan bangun**.
Di dalamnya ada dua putusan akuntansi yang hanya pemilik boleh ambil; membangun
lebih dulu berarti memutuskannya diam-diam.

**Lingkup verifikasi:** kode + enumerasi rute + probe baca-saja. Nol endpoint
destruktif dipanggil, nol perubahan.

## Cacatnya

FE desktop (`BankAccountPanel.tsx:720,732`) memanggil:
```
POST /api/bank-transactions/{id}/post
POST /api/bank-transactions/{id}/void
```
FE mobile (`BankAccountDetailPage.tsx:909,939`) memanggil ejaan LAIN:
```
POST /api/bank-accounts/transactions/{id}/post
POST /api/bank-accounts/transactions/{id}/void
```

**Keempatnya TIDAK ADA.** Tak ada endpoint yang mem-void atau menerbitkan satu
transaksi bank di BE, dengan ejaan apa pun.

## Bukti — DUA metode enumerasi + kontrol perilaku

1. **`app.routes` (aplikasi yang berjalan)** — di bawah `/api/bank-accounts`
   hanya 11 jalur: `GET`/`POST ""`, `/dropdown`, `/{id}` (GET/PATCH/DELETE),
   `/{id}/balance`, `/{id}/statement`, `/{id}/transactions` (GET+POST),
   `/{id}/adjust`. **Tak ada** `transactions/{id}/void|post`.
2. **Dekorator statis** — `bank_accounts.py` punya tepat 11 `@router.*`, tak
   satu pun `/transactions/{id}/...`. Satu-satunya dekorator tulis
   `/transactions/` di seluruh router ada di `intercompany.py` (tak
   berhubungan).
3. **Kontrol perilaku** (probe FRONTEND + milikku, saling bebas):
```
/api/bank-accounts/transactions/{id}/void   404 "Not Found"
/api/bank-transactions/{id}/void            404 "Not Found"
/api/rute-karangan/{id}/void                404 "Not Found"   <- karangan, IDENTIK
/api/bank-transfers/{id}/void               422 body.reason required
/api/bank-transfers/{id}/post               404 "Bank transfer not found"  <- rute ADA
GET /api/bank-accounts/{id}/transactions    200                            <- auth sehat
```

## ⚠️ JEBAKAN: 403 pada ejaan mobile BUKAN bukti keberadaan

Ejaan mobile menjawab **403**, dan aku sempat membacanya sebagai "berarti
rutenya ada". **Salah.** 403 itu dari pola **awalan tanpa jangkar**
`^/api/bank-accounts` (POST → `kas_bank/C`), yang menolak **sebelum** FastAPI
melakukan routing. Mencoba metode lain (PATCH/DELETE) tak menolong — pola itu
menelan setiap metode, jadi **tak ada probe yang bisa membedakan kedua
hipotesis**. Yang menyelesaikannya enumerasi, bukan probe.

Pembeda yang sahih, dan layak dipakai ulang:
- `{"detail":"Not Found"}` **polos** = rute tak ada
- `404` dengan **pesan objek** ("Bank transfer not found") = rute ADA, objeknya tidak
- `422` = badan sempat DIURAIKAN → lapis izin dilewati sepenuhnya

## Akibat nyata

- Tombol **batalkan-massal** dan **terbitkan-massal** di halaman rekening bank
  desktop memanggil ketiadaan. Medan `status` yang mendarat hari ini membuatnya
  **terjangkau**, bukan **berfungsi** — medannya benar dan tetap perlu, ia cuma
  tak cukup.
- Gerbang FE meninggalkan **2 transaksi Rp 1.337 yang TAK BISA DIBALIK**, justru
  karena jalur void-nya tak ada. Itu sisa permanen di data pemilik.
- Gerbang FE untuk situs itu **tak akan pernah bisa hijau** pada assertion
  "lanjut benar-benar mem-void". Sudah dikabari; assertion itu ditahan.

## DUA PUTUSAN AKUNTANSI — milik pemilik, bukan milikku

1. **Apakah mem-void transaksi bank menulis JURNAL PEMBALIK?** Transaksi bank
   punya `journal_id`. Kalau ya, void-nya tunduk Law 2 dan butuh Law 31 penuh.
   Kalau tidak, ia sekadar penandaan status — dan saldo bank harus dijelaskan.
2. **Apakah alasan pengguna WAJIB?** Dua permukaan FE saat ini **tak sepakat**:
   mobile mewajibkan alasan, desktop mengirim nol badan. Satu aksi, dua janji
   berbeda di layar.

Sampai keduanya diputuskan, membangun endpoint berarti aku yang memilih.

## Kalau nanti dibangun

Ukur lebih dulu apa yang sudah dilakukan tetangganya: apakah
`POST /api/bank-accounts/{id}/transactions` dan `/adjust` menulis jurnal —
supaya void-nya cermin dari pembuatannya, bukan karangan baru. Lalu satukan
ejaan kedua permukaan FE; dua ejaan untuk satu aksi adalah cacat tersendiri.

---

# PENGUKURAN 13 Sep 2026 — "pasang yang sudah ada" TIDAK AMAN

Premis sebelumnya: handler `kasbank_v2.py:1220` sudah patuh, tinggal dipasang.
**Terukur salah dalam arti yang menentukan:**

1. **`kasbank_v2` tak pernah dipasang** (`git log -S` di `main.py` kosong). Satu
   `APIRouter()` tanpa prefix, 11 rute. `include_router` di `/api` menabrak rute
   hidup (`GET /api/bank-accounts`, `GET /api/bank-accounts/{id}`,
   `POST /api/bank-accounts/{id}/transactions`, `POST /api/bank-transfers` +
   `/post` + `/void`) — FastAPI diam-diam memakai yang terdaftar duluan.
2. **Handler mem-void transaksi bank APA PUN milik tenant.** Dari 206 baris,
   `origin_type=MANUAL` tanpa referensi = **1** (Rp 10 jt, 5 Sep). Sisanya milik
   modul lain (DP 77, beban 38, faktur 6, tagihan 6, penerimaan 6, transfer 4,
   pembayaran tagihan 3, …). Void lewat pintu ini membalik jurnal tapi dokumen
   asal tetap. **R9 tetap 0 dan tak akan menangkapnya.**
3. **Tanpa pagar izin:** tak ada pola `^/api/bank-transactions` di
   `permission_middleware`; path tanpa pola diteruskan. (Juga masuk sapuan izin.)
4. **DUGAAN, belum diukur:** transaksi manual ber-`transaction_number` NULL →
   `RV-None`. Keunikan `journal_number` per tenant harus diukur sebelum bentuk.
5. `reversed_at` tak diisi; docstring berbohong ("marks the original journal as VOID").

Frekuensi: 1 transaksi manual tanpa referensi dalam ±5 minggu data (9 Agu–12 Sep),
tenant pemilik/uji — mengukur pemakaian saat menguji, bukan pasar.

**Putusan pemilik (13 Sep): A — pasang, dibatasi.** Kriteria terima: penyaring
hanya MANUAL tanpa referensi (tolak atas ketiadaan asal yang sah, bukan
daftar-hitam) · izin `kas_bank V` dua sisi · nomor jurnal tanpa
`transaction_number` · `reversed_at` · docstring · `add_api_route` satu fungsi ·
gerbang wajib menolak transaksi BERREFERENSI di sisi merah. **Bentuk dulu.**
