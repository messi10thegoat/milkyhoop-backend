# KONTRAK FE — Penjualan seragam (Penawaran · Penerimaan · Pengiriman · Proforma) — 20–21 Okt 2026

Rujukan untuk WORKSPACE/FRONTEND. Semua perubahan **aditif** (medan lama tidak berubah), **tanpa migrasi**, dihitung **SERVER** (FE hanya memformat — jangan hitung ulang tanggal/sisa/aksi di klien). Pola acuan: Pesanan (r288). Master `f1e99a35` (restart 21 Okt 2026 08:41:36Z). Pesanan/Faktur/Uang Muka/Nota Kredit di luar dokumen ini (BACKEND).

## 0. Aturan umum

| Hal | Aturan |
|---|---|
| Enum keadaan | selalu terisi (tak pernah null) pada daftar **dan** detail; nilai tak dikenal → FE jatuh ke logika lama |
| Pill `aksi` | satu kunci PALING mendesak per baris atau `null`; `?aksi=<kunci>` memakai predikat yang sama dgn hitungan `<kunci>_count` di `/summary`; hitungan 0 → FE sembunyikan |
| `documents[]` | `[{type}]` urut bundel, TANPA rekap; `type` ∈ `quotation · proforma · receipt · delivery · invoice · credit_note`; `doc_count == documents.length`. Sumber = fungsi yang sama dgn bundel `/…/{id}/documents` |
| `?search=` di `/summary` | memakai predikat yang SAMA dgn `?search=` daftar; membatasi hitungan/nilai (chip dalam konteks cari) |
| `with_totals=true` | opt-in; agregat atas SEMUA hasil filter (bukan halaman). FE kirim di muat pertama saja |
| Tanggal | "hari ini" = tanggal BISNIS tenant (zona tenant), bukan UTC/jam peramban |
| Bundel `/…/{id}/documents` | skema SAMA dgn bundel SO/faktur: `{anchor, default, groups[{key,label,docs[]}]}`; tiap dok punya `sent`, `batal`, `status`; jangkar SELALU tampil sebagai dirinya (draf=`draf`, batal=`batal`) |
| Paritas detail == baris | setiap medan keadaan/aksi/documents di baris ADA di detail dengan nilai sama (harness 485 detail) |

## 1. Penawaran — `GET /api/quotes`

Daftar (item) + detail membawa: `position_text` (kolom Status: Draf · Terkirim · Dilihat · Diterima · Ditolak · Jadi pesanan · Dibatalkan · **Kedaluwarsa** [sent/viewed lewat tanggal == is_expired]; fungsi SAMA dgn `anchor.position_text`) · `kedaluwarsa_terbuka` (sent/viewed ∧ bertanggal) · `kedaluwarsa_hari` (expiry − hari ini; null bila tak terbuka; <0 lewat) · `kedaluwarsa_keadaan` ∈ `telat | hari_ini | akan | selesai | tanpa` · `kedaluwarsa_teks` (`"Hari ini"` / `"Besok"` / `"24 Okt"` / `"12 Okt, lewat masa"` / `"—"`) · `aksi` (`perlu_dikirim · tunggu_jawaban · segera_habis · lewat_masa · perlu_dipesan`) · `documents`/`doc_count`.
- `telat` ⇔ `is_expired`. `selesai` = accepted/converted/declined bertanggal (tanggal redup). `tanpa` = draf/void/tanpa tanggal.
- `documents`: tanpa SO = `[quotation]`; sudah jadi SO = daftar dokumen SO itu (sama dgn kolom Dok. Pesanan).
- Daftar: `?with_totals=true` → `sum_total` (Σ total_amount hasil filter; **tanpa sum_sisa**). Paginasi: `skip/limit/page/offset` + `has_more`.
- `GET /api/quotes/summary?search=` ; pill count/value: `perlu_dikirim, perlu_dipesan, tunggu_jawaban, segera_habis, lewat_masa`.
- `GET /api/quotes/{id}/documents` (21 Okt): `anchor.kind="quotation"` (position_text = Draf/Terkirim/Dilihat/Diterima/Ditolak/Jadi pesanan/Dibatalkan/**Kedaluwarsa**; `total`=total_amount; `paid/balance = null`; sales_order_* bila sudah jadi SO). Penawaran SELALU tampil sebagai dirinya di kelompok `pnw` (draf → status `draf`, void → `batal` + `batal=true`, lainnya `terbit`). Belum jadi SO: hanya `pnw`. Sudah jadi SO: kelompok bundel SO-nya apa adanya (`rekap · pnw · pro · kw · ki · nk`, `sertakan_nk`). `default={group:"pnw",index:0}`. `documents[]` baris = bundel TANPA rekap dan TANPA `credit_note`. Izin: `quote` R.

## 2. Penerimaan Pembayaran — `GET /api/receive-payments`

- `?penerimaan_saja=true` — saring di SERVER (settlement_type `RECEIVE_PAYMENT`; membuang baris uang-muka/nota-kredit ber-id jurnal). Menggantikan filter klien `adaWrapper`; `total/has_more/with_totals` ikut benar. FE tetap boleh kirim `source_type=cash&include_voided=true`. Default false = bentuk lama.
- `?aksi=belum_dialokasikan`, `?with_totals=true` → `sum_total` (Σ total_amount **tanpa voided**) + `sum_sisa` (Σ belum_dialokasikan, hanya posted).
- Per baris + detail: `dialokasikan` (Rp; sel **"Ke faktur"** = Σ alokasi AKTIF, fungsi yang sama dgn `anchor.paid` bundel; hanya wrapper posted, lainnya 0; `dialokasikan + belum_dialokasikan == total_amount`) · `belum_dialokasikan` (Rp; **TURUNAN** = `receive_payments.total_amount` − Σ alokasi AKTIF; hanya wrapper posted, lainnya 0) · `aksi` (`belum_dialokasikan` | null) · `alokasi_keadaan` ∈ `belum | sebagian | penuh | tanpa` · `alokasi_teks` (`"Belum dialokasikan"` / `"Sebagian dialokasikan"` / `"Teralokasi penuh"` / `"—"`) · `invoice_count` (detail juga) · `documents`/`doc_count`.
- ⚠️ `allocated_amount` dan `unapplied_amount` (lama) membaca CACHE wrapper — basi untuk penerimaan yang alokasinya dilepas (kaos 6 baris, Rp 16.284.600). **Pakai `belum_dialokasikan`/`alokasi_*` untuk tampilan keadaan.** Koreksi kolom lama = tiket (angka uang, izin pemilik).
- Baris non-wrapper (jurnal uang-muka/nota-kredit; id = id JURNAL) → `alokasi_keadaan = tanpa`, `documents = []`.
- `GET /api/receive-payments/summary?search=` + `belum_dialokasikan_count`, `belum_dialokasikan_value`. `total_settled_noncash` tetap seluruh tenant (tak dibatasi search).
- `GET /api/receive-payments/{id}/documents`: `anchor.kind="receipt"` (position_text = alokasi_teks; total/paid/balance = total / Σ alokasi aktif / belum_dialokasikan); groups `rekap(+pnw bila semua faktur satu SO) · kw (kwitansi penerimaan ITU SENDIRI, posted/voided) · ki (SJ+faktur teralokasi) · nk`; `default` = `{group:"kw",index:0}` (null untuk draf). Izin: `receive_payment` R.

## 3. Pengiriman (Surat Jalan) — `GET /api/deliveries`

- Paginasi: `page/per_page` **dan** `skip/offset` (prioritas offset > page > skip) + `has_more`. Urutan seri deterministik (`, f.id`).
- `?aksi=perlu_resi|belum_dicatat`. **Tanpa with_totals/uang** (Pengiriman tanpa kolom uang).
- Per baris + detail: `serah_keadaan` ∈ `belum_dicatat | belum_diterima | diterima | tanpa` · `serah_teks` (`"Serah belum dicatat"` / `"Belum diterima"` / `"Diterima 17 Okt"` / `"—"`; **netral, tidak merah**) · `aksi` (`perlu_resi` > `belum_dicatat` | null) · `documents`/`doc_count`.
- `tanpa` = voided ATAU fitur `pengiriman_serah` OFF. `perlu_resi` = posted ∧ jenis `kurir` ∧ nol resi. `belum_dicatat` = posted ∧ (tak ada baris serah ∨ jenis kosong). `belum_diterima` BUKAN pill.
- `GET /api/deliveries/summary?search=` : `total_posted, total_voided, today_count, total_all` + `perlu_resi_count`, `belum_dicatat_count` (tanpa value).
- `GET /api/deliveries/{id}/documents`: `anchor.kind="delivery"`, `position_text = serah_teks`, **`total/paid/balance = null`**; groups `rekap · pnw · ki (Surat Jalan itu sendiri [voided pun] + faktur asal)`; `default={group:"ki",index:0}`. Izin: `sales_invoice` R.

## 4. Proforma — `GET /api/proformas`

- `?with_totals=true` → `sum_total` (Σ amount **tanpa cancelled**) + `sum_sisa` (Σ outstanding terbit belum lunas; == `summary.issued_outstanding_amount`).
- Per baris + detail: `jatuh_tempo_terbuka` (terbit ∧ SO tak batal ∧ belum lunas ∧ bertanggal) · `jatuh_tempo_hari` · `jatuh_tempo_keadaan` ∈ `telat | hari_ini | akan | selesai | tanpa` · `jatuh_tempo_teks` (`"Hari ini"` / `"Besok"` / `"28 Okt"` / `"12 Okt, lewat tempo"` / `"—"`) · `aksi` (`perlu_diterbitkan · menunggu_bayar · segera_tempo · lewat_tempo`) · `documents`/`doc_count`.
- `telat` ⇔ aksi `lewat_tempo`. `selesai` = terbit LUNAS bertanggal. `tanpa` = draf/batal/kedaluwarsa/terbit-pada-SO-batal/tanpa due_date.
- `GET /api/proformas/summary?search=`: semua hitungan/nilai/pill dibatasi search.
- `GET /api/proformas/{id}/documents`: `anchor.kind="proforma"`; `position_text` ∈ `Draf · Dibatalkan · Kedaluwarsa · Lunas · Dibayar sebagian · Menunggu bayar`; `total=nominal`, `paid/balance` hanya untuk terbit (else null); groups `rekap · pnw · pro (proforma itu sendiri) · kw (kwitansi uang muka bertaut)`; `default={group:"pro",index:0}`. Izin: `proforma` R.

## 5. Yang BELUM / tiket

Koreksi `allocated_amount`/`unapplied_amount` Penerimaan (turunan; izin pemilik) · `response_model` Pengiriman · pill `belum_diterima`/`perlu_difaktur` · paritas detail Uang Muka/Nota Kredit/Faktur (BACKEND S2) · pesan validasi default Pydantic masih Inggris (FE mencocokkan teksnya).

## 6. Bukti / alat

`scripts/ops/uji-nyata-{penawaran,penerimaan,pengiriman,proforma}-seragam.sh`, `uji-nyata-paritas-detail.sh` (485 detail = semua baris kaos+grapgrap), `uji-nyata-dokumen-faktur.sh` (bundel SO lama==baru 664). Semua baca-saja, kontainer sekali-pakai. Tes: `tests/unit/test_{penawaran,penerimaan,pengiriman,proforma}_seragam.py`, `test_paritas_detail.py`, `test_pengiriman_tiebreak.py`.

## 7. Idempotensi: replay hanya bila dokumen sumber masih ada (21 Okt 2026)

- Kunci idempotensi tersimpan = respons dokumen yang pernah dibuat. Dulu replay hanya memeriksa masa berlaku 24 j: draf yang DIHAPUS / penerimaan yang dibatalkan tetap di-replay -> 201 dengan id hantu, GET 404.
- Sekarang (`utils/idempotency.py`: `SUMBER_BUAT`, `sumber_basi`): dokumen TIDAK ADA -> permintaan diproses baru (semua jalur, kunci default maupun header). Dokumen VOIDED -> proses baru HANYA untuk kunci default-isi (POST /receive-payments, /bill-payments, /sales-invoices/{id}/payments tanpa header); kunci header klien + voided tetap replay (semantik retry).
- Header yang berlaku: `X-Idempotency-Key` (alias `Idempotency-Key`). Medan body `idempotency_key` di /receive-payments = metadata, TIDAK dipakai dedup.
- Source_type aksi (void/apply/fulfill/refund...) tidak diperiksa.
- Tiket terpisah (tak diubah): kunci `INVOICE_PAYMENT:{id}:{amount}` tanpa tanggal/bank men-dedup dua cicilan sah bernominal sama dalam 24 j. Terukur: grapgrap 0 kejadian, kaos 1 (nominal sama, beda hari).
