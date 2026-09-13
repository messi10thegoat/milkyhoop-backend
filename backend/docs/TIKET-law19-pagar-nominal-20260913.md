# TIKET — Law 19: pagar nominal dokumen (13 Sep 2026)

**Status:** **HEADER BEKU (V244) — BARIS BELUM.** Jangan tulis Law 19 "tutup".

## Kenapa ada

Konstitusi Law 19 mengklaim 5 trigger freeze (`trg_invoice_freeze`, `trg_bill_freeze`, …).
**Terukur dekoratif dua kali** (13 Sep 2026, `pg_trigger` + `pg_proc`): nol trigger, nol
fungsi. Nominal faktur/tagihan/beban/pembayaran yang sudah dibukukan bisa diubah dengan
satu `UPDATE` — dibuktikan lewat eksekusi (G1 di bawah).

## Yang dipasang — V244

- Fungsi `law19_bekukan_nominal()` + 5 trigger `trg_law19_bekukan_nominal`
  `BEFORE UPDATE … WHEN (OLD.journal_id IS NOT NULL)`.
- **Predikat `journal_id`, BUKAN `accounting_status`**: `accounting_status` terukur berbohong
  (receive_payments 5 posted-berjurnal tapi UNPOSTED; 9 tagihan check_13). `journal_id IS NOT
  NULL` ⇔ jurnal sumber ada: 159/159. `journal_id` ikut beku (13/13 penulisnya bertransisi
  dari NULL).
- **Perbandingan numerik** (100000 == 100000.00).
- Kolom beku: sales_invoices 6 · bills 17 · expenses 6 · receive_payments 2 · bill_payments_v2 6.
- Sengaja bebas: `amount_paid`, status*, fulfillment/revenue, `total_cogs`, void*,
  customer/vendor (merge), `allocated_amount`, `unapplied_amount`.

## Bukti

| gerbang | hasil |
|---|---|
| SQL uji kering (`scripts/gerbang_v244_{sql,lama,baru}.sql`) | **85/85**: G1 merah-lama 6 (ubah nominal BERHASIL di skema lama) · daftar kolom = daftar independen dua arah · 37/37 kolom ditolak satu per satu · journal_id→NULL ditolak 5 · T1 skala sama lolos 6 · T2 non-nominal lolos 5 · G3a 19 himpunan SET pasca-posting lolos · draf lolos 2 · G4 sabotase lolos + pulih ditolak · ROLLBACK V244 terbukti |
| SQL atas trigger HIDUP | **79/79** |
| Handler (`scripts/gerbang_v244_handler.py`) uji kering DAN hidup | **16/16**: bayar faktur (amount_paid +1000, status) · bayar tagihan draf→post (amount_paid 0→1000, partial; trigger DB lama ikut menyala) · void beban · void faktur · terapkan DP (amount_paid +1000) |
| jalur 6 terapkan nota kredit | **LABEL: fitur MATI SEBELUM PAGAR — terbukti tingkat SQL (G3a), handler TIDAK diuji.** Gagal identik dengan DAN tanpa pagar. Lihat `TIKET-nota-kredit-apply-mati-20260913.md` |
| terapkan kredit vendor | **TIDAK DIJALANKAN** — `vendor_credits` 0 baris (bukan lulus) |
| skrip harian | BROKEN 0; check_2 PASS_EXEMPT; hc_verdict 8/8 sesuai patok |

Kegagalan alat yang tertangkap (tak satu pun menyentuh V244): G4/RB mencatat hasil di dalam
SAVEPOINT (kelas V242, dibetulkan sebelum jalan); pid dibaca dari atribut padahal `data` dict;
pasangan pelanggan lewat kolom text; uuid=varchar tanpa cast; kontrol "nol trigger menetap"
dipatok ke fase uji kering lalu memerah atas trigger hidup (dibuat sadar-fase).

## SYARAT TERBUKA

1. **Tabel BARIS belum dipagari** (`sales_invoice_items`, `bill_items`, item beban). Sesudah
   V244: **header beku, baris masih bisa diubah → total header bisa tak sama dengan jumlah
   barisnya, dan tak ada yang berbunyi.** Unit terpisah (jalur tulis lain).
2. **Saat nota kredit dihidupkan, gerbang V244 WAJIB diulang untuk jalur itu**, dan
   pengecualian label jalur 6 di `gerbang_v244_handler.py` WAJIB dicabut.
3. Konstitusi Law 19 harus dikoreksi ("5 trigger" → V244, header saja) saat izin suntingan
   skill diberikan — kalau tidak, dokumen menjanjikan "nominal dibekukan" padahal separuhnya belum.

## Temuan sampingan (tidak diperbaiki)

- `bills_service.update_bill` (lama): penjaga `bill.get("status_v2") and status_v2 != 'draft'`
  **meloloskan status_v2 NULL** (1 baris hari ini, UNPOSTED tanpa jurnal). V244 menutupnya untuk
  dokumen berjurnal; celah aplikasinya tetap.
- `vendor_deposits.py:718` (terapkan DP vendor ke tagihan) menulis `bills.paid_amount` dan
  membaca `bills.total_amount` — **kedua kolom TIDAK ADA** di `bills`. **DUGAAN** endpoint selalu
  500 di UPDATE ini; eksekusi belum diukur. Kalau benar: fitur mati, bukan kerapian.
