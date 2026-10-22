# KONTRAK BE — Pembayaran Keluar (bill-payments) daftar seragam — 22 Okt 2026

Menutup celah WORKSPACE no. 2. Kembar `receive_payments` (Penerimaan, Penjualan seragam) + `bills` (KONTRAK-bills-daftar-seragam-20261022, LIVE 95d7b901).
Aditif, dihitung SERVER, FE hanya memformat. Sumber = wrapper `bill_payments_v2` + alokasi (unit (2) LIVE adb0d84c); dialokasikan/belum = TURUNAN alokasi.
Satu modul: `services/pembayaran_keluar_daftar.py` (+ `services/bayar_keluar_riwayat.py`).

## 0. Putusan (pola SaaS, tanpa tanya)

| # | Putusan | Acuan |
|---|---|---|
| P1 | `GET /bill-payments/{id}/documents` = **BUNDEL** (dulu salinan daftar lampiran). Lampiran tetap di `GET /{id}/attachments` (sudah ada). Diukur 22 Okt: 0 permintaan `GET /api/bill-payments/*` di log nginx 14 hari, FE tak memanggil `/documents` | Penjualan: `/receive-payments/{id}/documents` = bundel, lampiran di `/attachments` |
| P2 | Pill satu kunci `belum_dialokasikan` (== Penerimaan) | QBO "Unapplied payments" |
| P3 | `position_text` = `Draf` · `Batal` · teks alokasi (`Belum dialokasikan` / `Sebagian dialokasikan` / `Teralokasi penuh`) | jangkar Penerimaan = teks alokasi |
| P4 | Bundel: kelompok `faktur` hanya untuk pembayaran **posted** (alokasi aktif = posted; tabel alokasi tak berstatus). Pembayaran itu sendiri SELALU tampil (draf/batal sebagai dirinya) | Penerimaan: kwitansi diri sendiri + faktur teralokasi aktif |

## 1. `GET /api/bill-payments` (daftar)
Param **tambahan**: `?aksi=belum_dialokasikan` (422 nilai asing), `?with_totals=true` → `sum_total` (Σ total_amount bukan voided) + `sum_sisa`
(Σ `belum_dialokasikan`), angka (float), atas SEMUA hasil filter. `?search=` kini satu predikat dengan `/summary?search=`: tiap kata cocok salah satu dari
nomor, nama vendor, referensi, catatan, nama/kode vendor master, **nomor faktur pembelian teralokasi**.
Per item **tambahan**: `position_text` (P3), `documents` `[{type}]` (urut bundel), `doc_count`. Medan unit (2) tetap
(`dialokasikan`, `belum_dialokasikan`, `aksi`, `alokasi_keadaan`, `alokasi_teks`).

## 2. `GET /api/bill-payments/summary`
**Tambah** `?search=` (membatasi SEMUA hitungan/uang) + `belum_dialokasikan_count` / `belum_dialokasikan_value` (ungkapan SAMA dgn `?aksi=`).

## 3. `GET /api/bill-payments/{id}` (detail)
`position_text`, `documents`, `doc_count` dengan nilai SAMA dengan baris.

## 4. `GET /api/bill-payments/{id}/documents` (bundel) — DIGANTI (P1)
Skema SAMA `{anchor, default, groups[{key,label,docs[]}]}`.
- `anchor`: `kind="payment_out"`, `number`, `vendor`, `position_text`, `total` (= total_amount = kas keluar), `discount` (= discount_amount, potongan
  pelunasan), `paid` (= dialokasikan), `balance` (= belum dialokasikan), `vendor_phone`, `vendor_email`. Invarian: **total + discount = paid + balance**
  (alokasi dihitung dari bruto = kas + potongan, rumus tulis unit (2)).
- groups: `po` (PO asal tagihan teralokasi) · `bayar` (pembayaran ITU sendiri; status `draf | batal | dibayar`) · `faktur` (tagihan teralokasi; dok = `TD.dok_tagihan`,
  status SAMA dengan bundel `/bills/{id}/documents`) · `nota_debit` (ND bertaut tagihan itu, bukan draf/void).
- `default = {group:"bayar", index:0}`. Izin: `send_payment` R.

## 5. `GET /api/bill-payments/{id}/history` — BARU
Bentuk SAMA riwayat SO/PO/tagihan: `{success, data:{payment_id, payment_number, events[], total, omitted[]}}`. Kejadian: `PEMBAYARAN_DIBUAT` · `PEMBAYARAN_DIBAYAR`
(posted) · `ALOKASI_TAGIHAN` per tagihan (izin `purchase_invoice`, aktor = posted_by) · `PEMBAYARAN_DIBATALKAN` (voided_by + alasan) + audit_logs entitas
`bill_payments`. `/activities` lama tetap.

## 6. Bukti & alat
- Tes unit `tests/unit/test_bayar_keluar_kontrak_seragam.py`; `test_lampiran_1a_download` diperbarui (rute `/documents` kini bundel, lampiran tetap `/attachments`).
- Uji nyata `scripts/ops/uji-nyata-bayar-keluar-kontrak.sh`: grapgrap + kaos, satu transaksi SELALU ROLLBACK (kaos + 2 baris sintetis 'TES E2E' karena data nyata
  nol pembayaran ber-sisa): pasangan SQL `?aksi=` == Python, summary, with_totals, detail == baris, bundel == baris, dok tagihan lintas bundel sama, search, riwayat.
  Kontrol merah `MERAH=1`.
