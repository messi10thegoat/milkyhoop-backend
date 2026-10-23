# KONTRAK BE — Nota Debit (vendor credits) U3c: daftar + detail + bundel SERAGAM — RANCANGAN 23 Okt 2026 (BACKEND)

Acuan = Nota Kredit seragam (S2-NK, `services/nota_kredit_daftar.py`, `routers/credit_notes.py`; gold reference Penjualan). Uang tetap dari
JURNAL + aplikasi aktif (U3a/U3b LIVE: `services/nota_debit.sql_sisa_nd`, V441 cabang 4/5, V443 riwayat penerapan). U3c **tidak** mengubah
aturan uang, jurnal, atau migrasi — hanya medan turunan baca + rute baca. Server menghitung, FE memformat.

## 0. Peta NK ↔ ND (nama simetris bila maknanya sama)
| Konsep | Nota Kredit (Penjualan) | Nota Debit (Pembelian) | Catatan |
|---|---|---|---|
| sisa jurnal | `sql_sisa_nk` (net Piutang CREDIT_NOTE/_REFUND efektif; draf/void/bertaut = 0) | `sql_sisa_nd` (net Hutang VENDOR_CREDIT/_REFUND efektif; draf/void/bertaut = 0) | sudah LIVE U3a |
| pill aksi | `belum_terpakai` | `belum_terpakai` | SAMA: terbit ∧ tak bertaut ∧ sisa jurnal > 0 |
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
- Medan baris BARU: `aksi`, `sisa_keadaan`, `status_text`, `remaining_amount` (kini = sisa JURNAL, bukan cache), `terpakai`, `dikembalikan`,
  `original_bill_id/_number`, `purchase_order_id/_number`, `documents[]`, `doc_count`. Medan lama tetap.
- `?search=` per kata (nomor, vendor, ref_no, nomor tagihan asal) — pola `syarat_cari` faktur pembelian.

## 2. `GET /api/vendor-credits/summary` + `GET /{id}` (detail)
- summary menambah `belum_terpakai_count` / `belum_terpakai_value` (predikat SAMA dengan `?aksi=`), `total_applied` = Σ `terpakai` baris
  (turunan jurnal, SATU definisi dengan baris); `?search=` sama dengan daftar. Kunci lama tetap.
- detail == baris untuk semua medan §1 (paritas diuji).

## 3. `GET /api/vendor-credits/{id}/documents` (bundel)
Kelompok (urutan kembar NK: rekap/pnw → ki → nk), jenis bundel `debit_note` (**menunggu jawaban BACKEND2 K3 — dikutip di sini**):
`po` (PO asal lewat tagihan asal, bila ada) · `tf` "Terima & Faktur" (tagihan asal) · `nd` (nota debit ITU SENDIRI, apa pun statusnya).
Jangkar = nota debit: `paid`/`balance` **null untuk bukan-posted** (pola bundel bp 1e19a14f); posted: total = terpakai + dikembalikan + sisa.
`documents[]` baris = bundel tanpa jangkar (SATU definisi, `documents_baris`).

## 4. Bukti
- Tes unit berpasangan SQL == Python (aksi, sisa_keadaan, status_text), paritas detail == baris, pemindai medan.
- Harness nyata (kaos, transaksi luar SELALU ROLLBACK; data VC = 0 baris → baris sintetis TES E2E di dalam transaksi: draf, terbit bebas
  utuh, terbit sebagian dikembalikan, bertaut tagihan, batal) + **MERAH** (Python `aksi_nd` disabotase) + **MERAH_BOCOR** (satu baris lewat
  koneksi terpisah yang commit → net-0 WAJIB merah; dibersihkan + dibuktikan sisa 0) + net 0 sesudah proses.
