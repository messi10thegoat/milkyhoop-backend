# KONTRAK Kontak — identitas_text (BACKEND, 9 Nov 2026; MASTER, pemilik setuju)

`GET /api/customers/{id}` dan `GET /api/vendors/{id}` → `data.identitas_text` (aditif; medan lain tetap): satu baris server
**"<kode> · <HP> · <kota> · <termin>"**, contoh "V0003 · 0812 4306 5632 · Manado · Termin 30 hari".
- Bagian kosong dilewati.
- Kode: pelanggan `nomor_member`, vendor `code`.
- HP: penentu yang SAMA dengan `kontak_teks`. Vendor = mobile_phone ?? phone; pelanggan = telepon. Format kelompok 4 digit.
- Termin: "Tunai" bila 0/kosong, selain itu "Termin n hari".
- Penentu tunggal: `services/kontak_daftar.identitas_text`.
