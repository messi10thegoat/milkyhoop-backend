# Membaca modul Penjualan lain (baca saja di pilot)

Sumber: kartu agen laporan5/laporan6 (MASTER, BE c8c24092), sampel diverifikasi ke kode 24 Okt. Milkybook HANYA membaca modul ini di pilot; menulisnya lewat halaman masing-masing.
Semua nominal, sisa, status turunan, posisi, dan kunci `aksi` dihitung SERVER. Jangan menghitung sisa, umur, atau total sendiri.

## Pesanan Penjualan (SO)
- Tidak membuat jurnal. Status: draft, confirmed, partial_shipped, shipped, partial_invoiced, invoiced, completed, cancelled.
- Kode order generik terbit saat SO dikonfirmasi.
- Kunci `aksi` (services/so_aksi.py): telat_kirim, perlu_minta_dp, menunggu_pembayaran, perlu_dikonfirmasi, perlu_dikirim, siap_diserahkan, perlu_ditagih. Selama transisi, nilai aksi per baris bisa masih `menunggu_dp` (= menunggu_pembayaran).
- position_text dari server, mis. "Menunggu DP", "Menunggu pelunasan", "Perlu faktur", "Sebagian ditagih", "Lunas", "Batal".

## Proforma
- Penagih DP/termin/pelunasan atas satu SO. Tidak memposting jurnal. Terbayar dihitung dari uang muka, bukan disimpan.
- Status: draft, issued (Terbit), cancelled, expired. Tidak ada status "lunas"; lunas = terbayar >= nominal.
- Kunci `aksi`: perlu_diterbitkan, menunggu_bayar (= BELUM jatuh tempo), segera_tempo, lewat_tempo. Saring "belum_dibayar" = gabungan tiga kunci terbit.

## Faktur Penjualan
- Menimbulkan piutang dan jurnal saat terbit. Sisa piutang = turunan jurnal.
- Status tersimpan: draft, posted, partial, paid, void. `overdue`, `unpaid`, `active` hanya filter, bukan status tersimpan.
- Kunci `aksi` (services/faktur_aksi.py): perlu_diterbitkan, belum_lunas, segera_tempo, jatuh_tempo; saring jatuh_tempo_minggu_ini dan umur_*. Ambang segera = 7 hari (bawaan).
- position_text: Draf, Batal, Lunas, Sebagian, Belum lunas.
- Ringkasan piutang per pelanggan: ringkasan_piutang. Ringkasan jumlah/nilai: ringkasan_faktur.

## Penerimaan Pembayaran
- Status: draft -> posted -> voided. Penerimaan bisa dari kas atau dari uang muka (source_type).
- aksi: belum_dialokasikan.

## Uang Muka Pelanggan
- Status: draft -> posted -> partial | applied, atau void. status_detail turunan: partially_refunded, refunded.
- aksi: belum_diterapkan.

## Pengiriman (Surat Jalan)
- Status: posted | voided (tidak ada draf). Asal: dari faktur atau dari SO tanpa faktur.
- SJ tidak bisa dibuat untuk SO draft/cancelled/completed.
- aksi: perlu_resi, belum_dicatat, perlu_difaktur.

## Nota Kredit
- Status: draft, posted (terbit), partial (terpakai sebagian), applied (terpakai penuh), void.
