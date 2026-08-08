# P1: SO menyatakan "Ditagih 100%" padahal fakturnya masih DRAFT

**Tanggal:** 2026-08-06 **Severity:** P1 (counter turunan maju pada dokumen belum diposting)
**Status:** OPEN **Kelas bukti:** `[SQL]` untuk fakta; `[INFER]` untuk konsekuensi rollback

## Fakta terukur

Sesaat setelah "Buat Faktur" dari SO (faktur belum diterbitkan):

```
sales_orders.status        = 'invoiced'
sales_orders.invoiced_qty  = 100.0000        <- counter PENUH
sales_invoices.status      = 'draft'         <- belum terbit
sales_invoices.journal_id  = NULL            <- nol jurnal
compute_ar_outstanding()   = 0 baris         <- nol piutang di buku
journal_entries            = 8 (tidak bertambah)
```

UI menampilkan **"DITAGIH 100/100 · 100%"** dengan progress bar biru penuh, dan status pesanan
berubah jadi **"Ditagih"**.

Jadi: layar menyatakan penagihan **selesai seluruhnya**, sementara di buku besar **belum ada tagihan
sama sekali**.

## Kenapa ini bermasalah

1. **Sinyal "selesai" yang kuat di atas keadaan yang belum terjadi.** Progress bar penuh + status
   "Ditagih" adalah bahasa penyelesaian. Pengguna wajar menyimpulkan tak ada lagi yang perlu ditagih.
2. **Kelas yang sudah dilarang.** Iron Laws sudah menandai pola "dokumen draft ikut terhitung"
   (`status_v2 NOT IN ('draft','void')`, `b.status != 'void'` tanpa mengecualikan draft) sebagai
   pelanggaran di jalur baca angka. Ini instance yang sama pada counter SO.
3. **Risiko terkunci** — `[INFER]`, belum diuji: bila draft itu dibatalkan/dihapus, apakah
   `invoiced_qty` mundur? Kalau tidak, SO selamanya mengklaim sudah ditagih penuh padahal tak pernah,
   dan penagihan yang sah bisa terhalang.

## Yang BELUM diuji (sengaja)

Membatalkan/menghapus draft di tengah walkthrough akan mengotori tenant dan menguji hal lain.
Uji yang diperlukan:

```
Given SO confirmed, faktur draft dibuat dari SO (invoiced_qty = qty penuh)
When  faktur draft dibatalkan / dihapus
Then  sales_orders.invoiced_qty HARUS kembali 0
      DAN status SO kembali 'confirmed'
      DAN SO bisa ditagih lagi
```

Uji-bicara (Law 33): jalankan dulu pada skenario yang seharusnya LULUS (faktur diposting lalu di-void
→ counter mundur) supaya "merah" pada kasus draft benar-benar bermakna.

## Perbaikan yang disarankan

`invoiced_qty` / `status='invoiced'` seharusnya hanya maju pada faktur **POSTED**, bukan draft.
Kalau ada kebutuhan menampilkan "sedang disiapkan", pakai indikator terpisah (mis. "1 draft faktur")
— jangan pinjam counter penagihan.

## Catatan
Ditemukan pada langkah 4 walkthrough E2E 2026-08-06. Tidak memblokir alur — setelah faktur
diterbitkan semua angka benar (AR 5.000.000, jurnal Dr Piutang / Cr Pendapatan Diterima Dimuka).
