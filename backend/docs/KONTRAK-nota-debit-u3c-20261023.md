# KONTRAK BE — Nota Debit (vendor credits) U3c: daftar + detail + bundel SERAGAM — RANCANGAN r2 23 Okt 2026 (BACKEND)

Acuan = Nota Kredit seragam (S2-NK, `services/nota_kredit_daftar.py`, `routers/credit_notes.py`; gold reference Penjualan). Uang tetap dari
JURNAL + aplikasi aktif (U3a/U3b LIVE: `services/nota_debit.sql_sisa_nd`, V441 cabang 4/5, V443 riwayat penerapan). U3c **tidak** mengubah
aturan uang, jurnal, atau migrasi — hanya medan turunan baca + rute baca. Server menghitung, FE memformat.

## 0. Peta NK ↔ ND (nama simetris bila maknanya sama)
| Konsep | Nota Kredit (Penjualan) | Nota Debit (Pembelian) | Catatan |
|---|---|---|---|
| sisa jurnal | `sql_sisa_nk` (net Piutang CREDIT_NOTE/_REFUND efektif; draf/void/bertaut = 0) | `sql_sisa_nd` (net Hutang VENDOR_CREDIT/_REFUND efektif; draf/void/bertaut = 0) | sudah LIVE U3a |
| pill aksi | HANYA `belum_terpakai` (`AKSI_NK`; NK tak punya pill draf) | HANYA `belum_terpakai` | SAMA: terbit ∧ tak bertaut ∧ sisa jurnal > 0. Satu nama sejak hari pertama di baris `aksi`/`aksi_semua`, `?aksi=`, summary — tanpa alias. `perlu_diposting` TIDAK ada (NK tak punya) |
| aksi_semua | — (NK baris hanya `aksi`) | `aksi_semua` = `[aksi]` / `[]` | aditif, permintaan WORKSPACE (sama dengan baris tagihan) |
| remaining_amount | `sisa_kredit_nk` (routers/credit_notes.py:354: void 0 · bertaut & bukan draf 0 · selain itu total − applied − refunded); TIDAK beralih ke jurnal — digerbang == `sql_sisa_nk` | rumus LAMA tak diubah (routers/vendor_credits.py:259/436); digerbang == `sql_sisa_nd` di harness, semua keadaan | preseden NK = tanpa ubah makna; selisih di keadaan mana pun = lapor dulu, bukan diganti diam-diam |
| keadaan sisa | `sisa_keadaan` ∈ draf · utuh · sebagian · terpakai · dikembalikan · sebagian_dikembalikan · batal | SAMA | bertaut `posted` → `terpakai` (NetSuite "Fully Applied") |
| teks status | `status_text` = `tg.status_id("cn", …)` | `status_text` = `tg.status_id("vc", …)` (kata SAMA) | peta `vc` ditambah bila belum ada |
| rincian | `terpakai` / `dikembalikan` turunan jurnal (`rincian_nk_many`) | `terpakai` / `dikembalikan` (`rincian_nd_many`) | refund = VENDOR_CREDIT_REFUND |
| kaitan | `original_invoice_id/_number`, `sales_order_*` | `original_bill_id/_number`, `purchase_order_*` | |
| pihak | `customer_*` | `vendor_*` | |
| nomor/tanggal | `credit_note_number/_date` | `vendor_credit_number/_date` (medan lama dipertahankan) | |
| bundel | `GET /credit-notes/{id}/documents` | `GET /vendor-credits/{id}/documents` | kelompok §3 |
| riwayat | `GET /credit-notes/{id}/history` | `GET /vendor-credits/{id}/history` | dari riwayat penerapan V443 + jurnal |

## 1. `GET /api/vendor-credits` (daftar) — aditif
- `?aksi=belum_terpakai` (predikat SQL == Python `aksi_nd`), `?with_totals=true` → `sum_total` (Σ total bukan batal) + `sum_sisa` (Σ sisa jurnal).
- Medan baris BARU: `aksi`, `aksi_semua`, `sisa_keadaan`, `status_text`, `terpakai`, `dikembalikan`,
  `original_bill_id/_number`, `purchase_order_id/_number`, `documents[]`, `doc_count`. Medan lama tetap.
- `?search=` per kata (nomor, vendor, ref_no, nomor tagihan asal) — pola `syarat_cari` faktur pembelian.

## 2. `GET /api/vendor-credits/summary` + `GET /{id}` (detail)
- summary menambah `belum_terpakai_count` / `belum_terpakai_value` (predikat SAMA dengan `?aksi=`), `total_applied` = Σ `terpakai` baris
  (turunan jurnal, SATU definisi dengan baris); `?search=` sama dengan daftar. Kunci lama tetap.
- detail == baris untuk semua medan §1 (paritas diuji).

## 3. `GET /api/vendor-credits/{id}/documents` (bundel)
Kamus FE pembelian (WORKSPACE, `seragam/modul/dokumenPembelian.ts` KAMUS_BUNDEL_PEMBELIAN; kunci asing = GALAT di bacaBundel.ts):
kelompok `po` (PO asal lewat tagihan asal, bila ada) · `faktur` (tagihan asal) · `nota_debit` (nota debit ITU SENDIRI, apa pun statusnya,
TERAKHIR — kembar `nk` di bundel SO); jenis dokumen `purchase_order` · `bill` · `debit_note`; medan pihak kepala `vendor`. Status dokumen
dari himpunan draf · terbit · batal · lunas · diposting · dibayar (status baru = beri tahu WORKSPACE kunci + label dulu).
**Jawaban BACKEND2 (K3, 23 Okt):** "jenis NK di Penjualan persis `credit_note` (services/dokumen_bundel.py:221 nota_kredit; juga
DRAF_SEBAGAI_DIRINYA :32). Kunci kelompok `nk`, label 'Nota Kredit', urutan TERAKHIR. … `debit_note` saya setuju — simetris dengan
credit_note." (BACKEND2 menyarankan kunci kelompok `nd`; dipakai `nota_debit` karena kamus FE pembelian yang sudah ada menetapkannya.)
Jangkar = nota debit: `paid`/`balance` **null untuk bukan-posted** (pola bundel bp 1e19a14f); posted: total = terpakai + dikembalikan + sisa.
`documents[]` baris = bundel tanpa jangkar (SATU definisi, `documents_baris`).

## 4. Bukti
- Tes unit berpasangan SQL == Python (aksi, sisa_keadaan, status_text), paritas detail == baris, pemindai medan.
- Harness nyata (kaos, transaksi luar SELALU ROLLBACK; data VC = 0 baris → baris sintetis TES E2E di dalam transaksi: draf, terbit bebas
  utuh, terbit sebagian dikembalikan, bertaut tagihan, batal) + **MERAH** (Python `aksi_nd` disabotase) + **MERAH_BOCOR** (satu baris lewat
  koneksi terpisah yang commit → net-0 WAJIB merah; dibersihkan + dibuktikan sisa 0) + net 0 sesudah proses.

## 5. Di luar U3c (unit lanjutan, diusulkan)
Pratinjau per aksi (post/void/apply/unapply/refund: SEMUA blok + payload, kembar `/credit-notes/{id}/*/preview`) = **U3d**, sesudah U3c
(permintaan WORKSPACE). U3c hanya baca.
