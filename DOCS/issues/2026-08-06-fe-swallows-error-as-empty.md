# P1: FE menelan HTTP 500 dan menampilkannya sebagai "tidak ada hasil"

**Tanggal:** 2026-08-06 **Severity:** P1 (menuntun pengguna ke aksi yang merusak data)
**Status:** OPEN **Kelas bukti:** `[HTTP]` + `[CODE]` + tangkapan layar owner

## Gejala

Di **Faktur Pembelian → Tambah Item**, mengetik "kaos" tidak menampilkan apa pun. Yang muncul justru
tombol hijau besar:

```
+ 📦 Tambah Barang/Jasa Baru
  "Item baru akan tersimpan di master data"
```

Padahal produk "Kaos Biru 30s" **ADA** di master data.

## Yang sebenarnya terjadi

`GET /api/products/search/kulakan?q=kaos` mengembalikan **HTTP 500**
(`column p.content_unit does not exist` — sudah diperbaiki, commit `f29b694a`).
FE menangkap galat itu lalu merender **keadaan kosong**, bukan keadaan galat.

Bandingkan Faktur Penjualan yang sehat (`/api/items/autocomplete` → 200): produk tampil normal.
Owner menyimpulkan "autocomplete broken, padahal di faktur penjualan working" — diagnosis yang tepat,
tetapi penyebabnya tak terlihat dari layar sama sekali.

## Kenapa ini P1

Layar **secara aktif mengajak** pengguna membuat produk baru yang sudah ada. Akibat membuat duplikat
master data:
- WAC terpecah antara dua item → COGS salah
- Laporan persediaan/penjualan terbelah
- Sangat sulit dibersihkan setelah ada transaksi (FK ke bill_items/invoice_items)

Ini kelas **silent-fallback**: galat menyamar jadi keadaan normal yang plausibel. Persis pola yang
sudah tercatat sebagai musuh utama di rails proyek ("FE menelan 500 → items=[] → 'Semua item sudah
dikirim'"). Ini instance baru dari pola yang sama, artinya penambalannya selama ini per-kasus.

## Perbaikan yang disarankan

1. **Bedakan tiga keadaan** di setiap picker/list: *memuat* · *kosong (sukses, 0 hasil)* ·
   **gagal (galat)**. Jangan pernah memetakan galat ke "kosong".
2. Pada keadaan gagal: tampilkan pesan + tombol **"Coba lagi"**, dan **sembunyikan** ajakan
   "Tambah Barang/Jasa Baru" — jangan menawarkan aksi destruktif di atas informasi yang tidak valid.
3. Audit menyeluruh: cari `catch` yang men-set state kosong tanpa membedakan galat, terutama pada
   picker item/pelanggan/vendor.

## Uji regresi (harus bisa MERAH — Law 33)

Paksa endpoint mengembalikan 500 (mis. lewat mock/route intercept), lalu pastikan UI menampilkan
keadaan **galat**, bukan keadaan kosong. Test yang hanya diuji pada jalur 200 tidak membuktikan apa
pun tentang kelas bug ini.
