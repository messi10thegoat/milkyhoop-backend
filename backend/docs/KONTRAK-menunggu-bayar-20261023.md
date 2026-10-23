# KONTRAK "Menunggu bayar" SO + "Belum dibayar" Proforma (BACKEND2, 23 Okt 2026)

Status: DITERIMA MASTER 23 Okt dengan putusan: kunci SO = `menunggu_pembayaran` (satu kunci = satu makna di seluruh sistem); label pill partisi Proforma `menunggu_bayar` → "Belum jatuh tempo" (FE); kolom Status proforma "Menunggu bayar" tetap. Asal: pemilik (tangkapan grapgrap), MASTER 23 Okt.
Pola: Odoo prepayment 1–100% / NetSuite Customer Deposit / Zoho Retainer Invoice "Unpaid" — satu daftar "menunggu
pembayaran sebelum pemenuhan", bukan filter khusus DP. Iron Law 1/16 (terbayar = terbayar_proforma kanonik), Law 34.

## 1. Hasil ukur kanonik (baca-saja, terbayar_proforma + ringkasan_pesanan, 23 Okt)

| Tenant | menunggu_dp kini | menunggu_bayar usulan | Tambahan |
|---|---|---|---|
| kaos | 1 SO / 150.000 (SO-2610-0007) | 6 SO / 1.468.000 | 5 SO PELUNASAN (3 PENUH: SO-2609-0262, SO-2610-0003, SO-2610-0004); SO-2610-0007 150.000 → 300.000 (DP + PELUNASAN belum dibayar) |
| grapgrap | 1 SO / 1.695.000 (SO-2609-0019) | 2 SO / 4.575.000 | SO-2610-0036 PELUNASAN PENUH 2.880.000 |

Keluar dari menunggu_dp: 0. SO menunggu_bayar ber-faktur terbit (risiko hitung ganda sum_in): 0 di kedua tenant.
PELUNASAN dilewati karena faktur: 0. Himpunan SO dari proforma belum-dibayar == himpunan menunggu_bayar (selisih 0).
Koreksi kata audit tahap 1: SO tambahan BUKAN di luar SEMUA pill — di luar semua pill PEMBAYARAN (mereka ada di
perlu_ditagih ×4, telat_kirim ×1, perlu_dikirim ×1).

## 2. TEMUAN: kunci `menunggu_bayar` SUDAH ADA di daftar Proforma dengan makna lain

`services/proforma_aksi.AKSI = (perlu_diterbitkan, menunggu_bayar, segera_tempo, lewat_tempo)` = PARTISI jatuh tempo:
proforma `menunggu_bayar` = terbit belum lunas dengan jatuh tempo kosong atau > hari ini + N (BUKAN semua yang belum
dibayar). Maka:
- Proforma "Belum dibayar" (syarat MASTER (a)) = kunci SARING baru `belum_dibayar` = menunggu_bayar ∪ segera_tempo ∪
  lewat_tempo (terbit, SO tak batal, tak lunas: `aksi_proforma(...) in` tiga kunci itu). BUKAN pill; partisi tetap.
  "Lewat jatuh tempo" tetap subset (pill lama).
- SO memakai kunci `menunggu_bayar` (nama yang pemilik minta). Kunci SAMA di dua modul dengan makna berbeda = sumber
  salah baca. Usul: SO tetap `menunggu_bayar` (modul berbeda, tautan selalu {modul, aksi}); KONTRAK + docstring
  mencatat beda maknanya. Alternatif bila MASTER menolak: SO `menunggu_pembayaran`. → PUTUSAN MASTER.

## 3. Definisi SO `menunggu_pembayaran` (label FE "Menunggu bayar") (menggantikan `menunggu_dp` sebagai pill)

menunggu_pembayaran(SO) = SO berjalan (bukan draft/cancelled/completed) ∧ sisa SO > 0 ∧ ∃ proforma p milik SO dengan
`belum_dibayar(p)` ∧ ¬(p.purpose = PELUNASAN ∧ SO punya faktur terbit) [aturan tagihan_pertama yang sudah ada: begitu
faktur terbit, faktur yang menagih → pill faktur]. Nilai = Σ sisa kanonik (nominal − terbayar) proforma yang memenuhi.
- `perlu_minta_dp` TETAP (syarat: tanpa proforma terbit apa pun) → saling lepas dengan menunggu_bayar.
- Satu fungsi murni `so_posisi.menunggu_bayar(...)`; `menunggu_dp` (Posisi teks "Menunggu DP") tetap untuk TEKS kolom
  Posisi (Menunggu DP / termin / tagihan tak berubah).
- Syarat harness (MASTER (a)): himpunan SO menunggu_bayar == {SO dari proforma belum_dibayar} DIKURANGI pengecualian
  yang tertulis (SO tak berjalan/sisa 0/PELUNASAN+faktur), tiap pengecualian dicetak; hari ini selisih 0.

## 4. Alias & transisi (expand-contract, syarat MASTER (d))
- `?aksi=menunggu_dp` diterima = `menunggu_bayar`; `/summary` mengirim `menunggu_bayar_*` DAN `menunggu_dp_*` (= nilai
  menunggu_bayar) selama transisi; nilai `aksi`/`aksi_semua` baris tetap `menunggu_dp` sampai FE pindah (pola
  segera_tempo/jatuh_tempo_minggu_ini). Cabut alias sesudah FE pindah TERVERIFIKASI + nol pemanggil terbukti.
- `KUNCI_DP` → `KUNCI_BAYAR = (perlu_minta_dp, menunggu_pembayaran)`; kartu dashboard (?tugas=dp_belum_diterima, nama kunci tugas
  tetap) = menunggu_pembayaran ∪ perlu_minta_dp, judul server "N pesanan menunggu bayar"; label kartu FE → "Menunggu
  bayar".

## 5. Dashboard pengingat (KONTRAK-dashboard-pengingat §10 baru)
- Registri: ("sales_orders", "menunggu_dp", "DP ditunggu") → ("sales_orders", "menunggu_pembayaran", "Menunggu bayar").
  `key` pengingat memuat kunci → status "selesai"/dismiss untuk kunci lama TERSETEL ULANG sekali (diterima MASTER (c)).
- `sum_in_ditagih` NAIK (proforma PELUNASAN belum dibayar = sudah ditagih): kaos +1.318.000, grapgrap +2.880.000
  (terukur). `sum_in_dp_belum_ditagih` (perlu_minta_dp) tak berubah. Hitung ganda faktur+proforma: 0 hari ini; aturan
  (MASTER (b)): PELUNASAN dilewati bila faktur terbit (sudah di definisi); DP + faktur pada SO yang sama diukur harness
  (bila ada → sisa proforma yang sudah tercakup faktur keluar).

## 6. FE (WORKSPACE, sesudah kontrak final)
CW recipes/salesOrder.ts:122 `{label:'Menunggu DP', kunci:'menunggu_dp'}` → `{label:'Menunggu bayar', kunci:'menunggu_pembayaran'}`
(+ aksiSo.test.ts); saring Proforma "Belum dibayar" `?aksi=belum_dibayar` (+ summary belum_dibayar_count/_value); label pill partisi Proforma
`menunggu_bayar` → "Belum jatuh tempo" (recipes/proforma.ts:59 + peta warna :68 + kata cocok :24/:42 + tes).

## 7. Berkas & pemilik
so_posisi / so_aksi (BACKEND2), pengingat (BACKEND2), proforma_aksi + routers/proformas (BACKEND2);
routers/sales_orders.py Literal `aksi` + schemas/sales_orders.py summary (BACKEND — aditif, dikabari).
