# TIKET — pembayaran multi-alokasi dihitung ganda (TUTUP V245+V246) · lebih bayar AR mati (TERBUKA) · uuid-vs-varchar pihak (KELAS)

13 Sep 2026.

## 1. Multi-alokasi — TUTUP (V245 + V246, satu transaksi DB)

**Buku besar pemilik TIDAK salah; yang salah laporan per dokumen.**

Terukur lewat eksekusi (handler nyata, ROLLBACK): pembayaran 2.000 dengan dua alokasi 1.000:

| lapis | AP | AR |
|---|---|---|
| GL jurnal pembayaran | **benar** (Dr PAYABLE 2.000) | **benar** (Cr RECEIVABLE 2.000) |
| cache amount_paid per dokumen | benar (+1.000, inkremental) | **ganda (+2.000)** — dihitung ulang dari fungsi → faktur bisa tampil **"lunas" palsu** |
| compute_*_outstanding per dokumen | **ganda (−2.000)** | **ganda (−2.000)** |

Mekanisme: CTE `payment_debits` (AP) dan branch 1 `payment_credits` (AR) menjumlahkan SELURUH sisi AP/AR
jurnal pembayaran per dokumen yang punya alokasi, tanpa `amount_applied`. Kedua form FE membangun
multi-alokasi (`usePaymentOutFormState.ts:133`, `useReceivePaymentForm.ts:289`). Data historis: 0 pembayaran
multi-alokasi → nol dokumen berubah.

**Pemeriksa mewarisi cacat yang ia cegah:** `hc_ap_members` (V242, check_7) memakai bentuk join yang sama →
ikut ganda; pada data tanpa multi-alokasi keduanya tampak sepakat. Sesudah V245 saja, check_7 memerah PALSU.

**Perbaikan:**
- V245 — kedua fungsi PROPORSIONAL: bagian = ROUND(sisi AP/AR jurnal × applied / Σapplied, 2), sisa sen ke
  alokasi id terbesar. Dibangkitkan dari definisi hidup; ROLLBACK = definisi hidup apa adanya.
- V246 — `hc_ap_members` proporsional dengan **ekspresi SENDIRI** (subkueri berkorelasi, net jurnal efektif
  milik pemeriksa; tanpa CTE/fungsi jendela V245) — dua implementasi independen yang wajib sepakat.

**Prasyarat terukur sebelum rumus ditulis (G-A/G-C):** debit AP / kredit AR jurnal = Σ applied pada lebih bayar
AP (sisa ke 1-10550), diskon AP (ke 5-10200), biaya bank AP (5-20850), diskon AR (4-10200) → bagian tak
teralokasi TIDAK ikut dibagi.

**Gerbang `scripts/gerbang_v245_v246.py` 28/28** (satu transaksi, ROLLBACK): merah lama AP & AR · independensi
rumus pemeriksa · IDENTITAS (per dokumen seluruh tenant AP 11 / AR 6 sama; hc_verdict 8 sama; verify_ar_all sama;
patok V242 ap_invariant identik s/d sidik jari `a610101a…`) · hijau AP & AR (cache AR +1.000; R8 AP diam;
check_7 PASS_EXEMPT; verify_ar pelanggan PASS) · tak simetris 1.500+500 · G-A AP · G-C tiga · G-B POST lalu VOID
kembali persis (AP & AR) · G-A AR TIDAK DIJALANKAN (jalur mati, lihat 2) · sabotase definisi lama → −2.000 ·
**sabotase dua arah terpisah** (fungsi lama + pemeriksa baru → check_7 merah; pemeriksa lama + fungsi baru →
check_7 merah) · gabungan tak simetris check_7 PASS_EXEMPT.

**Hidup:** skrip ukur yang membuktikan cacatnya (`ukur_multi_alokasi.py`, `ukur_multi_ar.py`), tak diubah:
AP −2.000 → −1.000 per tagihan, check_7 PASS_EXEMPT; AR −2.000 → −1.000, cache +2.000 → +1.000, verify_ar PASS.

**Catatan sifat penjaga, jangan dicampur:** `verify_ar_reconciliation` BENAR untuk multi-alokasi (menangkap drift
2.000 di kode lama), tetapi tetap BUTA terhadap piutang tak teratribusi (MANUAL, CN tanpa faktur asal) —
`TIKET-verify-ar-reconciliation-buta-20260913.md`.

## 2. Lebih bayar AR (DP otomatis) — TERBUKA, unit DESAIN

Setiap penerimaan lebih bayar **500**; 0 DP otomatis pernah terbentuk. Kegagalan NOL tulis (terbukti).
Lapis (jalur mati ditelusuri sampai ujung sebelum ukurannya dinyatakan — galat pertama hanyalah dinding pertama):

1. **TERBUKTI** — `customer_id` UUID dikirim ke `customer_deposits.customer_id` VARCHAR.
2. **TERBUKTI** — `payment_method` penerimaan (`cash`/`bank_transfer`) melanggar `chk_cust_deposit_method`
   (`cash`/`transfer`/`check`/`other`).
3. **PASTI dari definisi** — `compute_deposit_remaining` membaca jurnal ber-`source_id = deposit_id`; DP otomatis
   tak punya jurnal sendiri (kewajiban DP ada di jurnal PENERIMAAN) → sisa selalu 0 → tak bisa diterapkan.
4. **DUGAAN** — `customer_deposits.account_id` diisi `bank_accounts.id`, padahal refund/void DP memakainya sbg CoA.

Patch lapis 1 dibuang (tak dikomit) supaya tak terbaca "sudah dibetulkan".
**Saat dihidupkan: gerbang V245 G-A AR WAJIB diulang.**
Catatan alat: sabotase yang meng-assert kode status tanpa JENIS galat tak membedakan apa pun ketika sisi hijau
belum hidup (`gerbang_lebih_bayar.py` "500 lagi" lulus karena kode baru pun 500).

## 3. KELAS — kolom pihak uuid di satu tabel, varchar di tabel lain

Tiga instans dalam satu hari: terapkan nota kredit (banding UUID vs str → selalu 400), helper DP (butuh
normalisasi), DP otomatis lebih bayar (tulis UUID ke varchar → 500). Tipe: `sales_invoices`/`receive_payments`/
`bills`/`bill_payments_v2`/`vendor_*` = uuid; `credit_notes.customer_id`, `customer_deposits.customer_id` = VARCHAR.
Skill invoices masih menulis `receive_payments.customer_id` VARCHAR (Law 34). **Sapuan belum dilakukan**: setiap
situs yang MENULIS nilai pihak dari kolom uuid ke varchar, atau MEMBANDINGKAN keduanya. Instans keempat hampir
pasti ada. Helper `services/pihak_helpers.py` adalah bentuk kanonik untuk perbaikannya.
