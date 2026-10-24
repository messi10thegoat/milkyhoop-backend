# Penawaran (Quote) di MilkyHoop

Diukur dari kode BE `c8c24092` (routers/quotes.py, schemas/quotes.py, schemas/items.py, schemas/customers.py). Aksi yang sah untuk satu dokumen SELALU dibaca dari server (detail_penawaran), bukan dari kartu ini.

## Alur
draf -> terkirim (sent) -> dilihat pelanggan (viewed) -> diterima (accepted) / ditolak (declined)
Penawaran diterima atau terkirim bisa dijadikan pesanan penjualan (SO) atau langsung faktur. Sesudah itu penawaran berstatus converted / invoiced.
Status yang ada: draft, sent, viewed, accepted, declined, expired, converted, void, invoiced.

## Aturan per aksi (dari kode)
- Ubah: hanya draf, terkirim, dilihat (STATUS_BOLEH_UBAH). Sesudah dijawab pelanggan tidak bisa diubah.
- Hapus: hanya draf yang belum pernah terkirim/dibagikan. Selain itu: batalkan.
- Kirim: dari draf atau terkirim.
- Terima: dari terkirim atau dilihat.
- Batal (void): dari draf, terkirim, dilihat, diterima. Batal penawaran yang sudah diterima WAJIB alasan. Tidak bisa batal bila sudah dikonversi atau ada uang muka.
- Jadikan pesanan: dari terkirim, dilihat, atau diterima.
- Kedaluwarsa: turunan tanggal berlaku (expiry_date) dan tanggal bisnis server, bukan tombol. Penawaran lewat masa tetap bisa diubah tanggalnya selama statusnya masih boleh diubah.

## Isi penawaran
- Wajib: tanggal penawaran, pelanggan (customer_id + nama persis dari master), minimal satu baris.
- Baris: keterangan, jumlah (> 0), harga satuan; opsional item_id, satuan, diskon persen, kode/tarif pajak.
- Pajak diatur PER BARIS (tax_id + tax_rate). "Tanpa pajak" untuk satu penawaran = tarif 0 di baris penawaran itu; setelan PKP usaha dan pelanggan tidak berubah.
- Diskon dokumen: fixed (rupiah) atau percentage.
- Uang muka di penawaran hanya angka di dokumen (tidak membuat jurnal).
- Nomor penawaran dibuat server bila kosong.
- Total, pajak, dan subtotal SELALU dari hitung_penawaran / pratinjau. Jangan hitung sendiri.

## Kekhasan MilkyHoop
- Kode order generik (kode_order) baru terbit saat pesanan (SO) dikonfirmasi, bukan saat penawaran dibuat.
- Pelanggan dan barang bisa dibuat langsung dari alur penawaran (master inline). Di Milkybook: cari dulu, bila tidak ada usulkan pelanggan/barang baru, lalu penawaran. Masing-masing satu kartu Tinjau.
- Pelanggan baru dibuat TANPA saldo awal piutang; barang baru TANPA stok awal (keduanya membuat jurnal, diurus di halaman masing-masing).
- Jenis barang: goods (stok dilacak), non_inventory (barang tanpa stok), service (jasa). Untuk non_inventory TANYAKAN: "Dikirim ke pelanggan?" -> bisa_dikirim (dikirim lewat Surat Jalan, tanpa stok/jurnal). Jasa tidak pernah bisa dikirim.
- Banyak usaha memakai barang non_inventory untuk semua barangnya; jangan menyarankan stok bila pengguna tidak meminta.
- Daftar penawaran punya pill AKSI dari server: perlu_dikirim, tunggu_jawaban, segera_habis, lewat_masa, perlu_dipesan. Pakai ini untuk pertanyaan "mana yang perlu ditindaklanjuti".

## Bahasa layar
Penawaran, Pesanan (SO), Faktur, Pelanggan, Barang & Jasa, Tinjau, Setuju, Ubah, Batalkan, Draf, Terkirim, Dilihat, Diterima, Ditolak, Kedaluwarsa, Dikonversi.
