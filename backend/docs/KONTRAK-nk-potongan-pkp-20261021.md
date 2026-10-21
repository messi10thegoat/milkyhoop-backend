# KONTRAK — NK potongan BRUTO di tenant PKP (21 Okt 2026)

Putusan pemilik LANGSUNG (AskUserQuestion sesi BACKEND, 21 Okt 2026): nominal potongan yang diketik = BRUTO (termasuk PPN); di tenant
PKP server memecahnya menjadi harga jual + PPN keluaran menurut komposisi pajak FAKTUR ASAL, sehingga PPN keluaran turun sebesar porsi
pajaknya. Modul: `services/nk_potongan_pajak.py`; titik pemicu tunggal `routers/credit_notes._cn_doc` (buat, pratinjau, sunting draf).

## Kapan berlaku
- reason ∈ {discount, pricing_error} ∧ faktur asal ada ∧ semua baris nominal polos (tanpa item_id / baris faktur / kode atau tarif pajak /
  diskon) ∧ tanpa diskon/tarif header ∧ tenant `is_pkp = true` ∧ faktur asal ber-PPN.
- Selain itu (non-PKP — grapgrap, kaos —, faktur tanpa PPN, NK retur barang, tarif eksplisit): kalkulator lama PERSIS.

## Rumus (Law 9 Decimal HALF_UP 2dp)
- Kelompok faktur = baris per (kode pajak, tarif) + ongkir (kode eksplisit, atau kode barang bertarif sama bila tepat satu = ongkir null
  IKUT barang). Angka TERSIMPAN faktur: B bruto, T PPN, H harga jual (sesudah diskon dokumen), D DPP dikenai tarif.
- G dibagi pro-rata menurut B (`allocate`, kelompok terakhir menyerap sisa) → Σ G_i = G.
- PPN_i = q2(G_i × T/B); harga jual_i = G_i − PPN_i; DPP_i = q2(harga jual_i × D/H). **Σ(harga jual + PPN) = G persis.** PPN_i bisa
  berbeda ≤ 1 sen dari q2(DPP_i × tarif) (tak semua bruto terjangkau rumus eksklusif); total dijamin.
- Satu baris pengguna → satu baris NK per kelompok (deskripsi sama, qty 1, unit_price = harga jual, tax_code_id/tax_rate/dpp/tax_amount).
- Posting TAK berubah: Dr 4-10200 (atau Pendapatan Diterima Dimuka V312) harga jual · Dr PPN Keluaran (VAT_OUTPUT) PPN · Cr Piutang G;
  baris DTL per kode. Plafon "melebihi sisa faktur" membandingkan G.
- Sunting draf tanpa baris baru: baris potongan terpecah digabung kembali jadi satu baris bruto lalu dipecah ulang (`gabung_tersimpan`).

## Bukti
`scripts/ops/uji-nyata-nk-potongan-pkp.sh` (kaos, transaksi ROLLBACK, is_pkp/faktor hanya di dalam tx; `MERAH=1` kontrol merah):
11% + ongkir ikut + diskon dok · 12% × 11/12 + baris tanpa pajak · bruto Rp 1 · kontrol non-PKP. Tes: `tests/unit/test_nk_potongan_pkp.py`.
Tidak masuk penjaga gabungan (menulis di transaksi ROLLBACK; penjaga itu baca-saja).

## Di luar cakupan (tiket)
Dokumen pajak Coretax untuk potongan sesudah faktur pajak terbit (kemungkinan faktur pajak pengganti, bukan nota retur) — dicatat di
tiket izin faktur pajak dari NK; tidak dibangun.
