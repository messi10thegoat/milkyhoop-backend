# DESAIN — pratinjau Nota Kredit ke pola penentu (NK-RENCANA), 23 Okt 2026, BACKEND

Basis: master de272a95, `routers/credit_notes.py` (3847 baris). Preseden: ND U3d (`services/nota_debit_rencana.py`).

## 0. Keadaan sekarang (diukur dari kode)
- `_pratinjau_nk` :3586 dan `_pratinjau_nk_aksi` :3746 menjalankan INTI PENULIS di savepoint, membaca jurnal yang terbentuk
  (`created_at = now()`), lalu me-rollback. Akibatnya pratinjau:
  - mengambil kunci tulis: `CREDIT_NOTE:`, `CREDIT_NOTE_APPLY:`, `CREDIT_NOTE_REFUND:`, `CN_RETUR_FAKTUR:`,
    `INVOICE_FULFILL:`, dan `FOR UPDATE OF sii`;
  - memanggil `get_next_journal_number` / `generate_customer_deposit_number`. Nomornya kembali karena penghitung
    berupa tabel, tetapi pratinjau tetap MENUNGGU kunci baris penghitung milik penulis yang sedang berjalan;
  - menulis `inventory_ledger`, `bank_transactions`, `customer_deposits` (KRD), `document_tax_lines`, `audit_logs`,
    lalu me-rollback.
- Penentu yang ada (`_rencana_nk`, `_rencana_nk_aksi`) pun sudah mengambil kunci:
  - `periksa_bisa_dinotakan` → advisory `CN_FAKTUR:`;
  - `pulihkan_saat_void(periksa_saja=True)` → `kunci_faktur` `INVOICE_FULFILL:`.
- Blok yang hanya muncul DI TENGAH penulisan (tak dikumpulkan penentu) ikut tampil sebagai `<AKSI>_REJECTED`:
  - posting: retur tanpa pengiriman / retur melebihi sisa terkirim (:2160/:2171), PPN non-PKP 422 (:2010),
    akun REVENUE_DEFERRED tak ada (:1939), akun inti tak ada (:1900);
  - void: `CN_VOID_AFTER_DELIVERY` (sudah di penentu).

## 1. Bentuk baru — `services/nota_kredit_rencana.py`
Satu fungsi penentu per aksi. Tanda tangannya: `(conn, tid, cn_row, masukan, *, kunci: bool) -> dict`. Isinya
`{blocks: [Blok...], ...rencana}`. `Blok` memakai ulang `nota_debit_rencana.Blok`, berisi code / status / detail
(detail apa adanya, str ATAU dict).

| aksi | penentu | rencana yang dikembalikan (dipakai PENULIS dan PRATINJAU) |
|---|---|---|
| post | `rencana_posting_nk` | `baris_posting_nk` = Dr Dimuka (porsi tertunda) / Dr Retur atau Diskon / Dr PPN / Cr Piutang (≤ sisa AR) / Cr Uang Muka Pelanggan (kelebihan → KRD); `porsi` cn_tertunda; `restock` = [(produk, qty, unit_cost, gudang)] + `baris_hpp_nk` (Dr Persediaan per baris / Cr HPP per akun); `dtl` per kode PPN; `krd` = {jumlah} |
| apply | `rencana_terapkan_nk` | faktur target, total_cn, application_date |
| unapply | `rencana_lepas_nk` | aplikasi aktif (id/faktur/jumlah/tanggal), alasan |
| refund | `rencana_refund_nk` | `baris_refund_nk` (Dr Piutang / Cr akun terpecahkan), akun dan rekening cermin, `cermin` (withdrawal −int) |
| void | `rencana_batal_nk` | draf → `hapus: True`; selainnya baris pembalik jurnal NK (tukar D/K dari journal_lines), baris pembalik companion COGS-CN, KRD yang ikut di-void, porsi tertunda yang dipulihkan |

Aturan bersama (sama dengan ND U3d):
- **Penulis**: kunci lama tetap di tempat dan urutannya tidak berubah (`CREDIT_NOTE:` → baca cn → penentu
  `kunci=True`). Blok pertama dilempar sebagai HTTPException dengan status dan detail YANG SAMA. Urutan blok =
  urutan `raise` lama, jadi galat pertama tetap galat yang sama. Sesudah itu penulis menulis DARI rencana:
  - nomor jurnal dan nomor KRD diambil di titik yang sama seperti sekarang;
  - `record_inventory_inbound`, `create_bank_transaction_for_journal`, `catat_porsi`, dan `pulihkan_saat_void`
    tetap dipanggil penulis.
- **Pratinjau**: `conn.transaction(readonly=True)`, `kunci=False`, tanpa advisory lock, tanpa FOR UPDATE, tanpa
  memanggil penomoran, nol tulis. Semua blok DIKUMPULKAN, termasuk blok yang dulu baru muncul di tengah penulisan
  (restock / PPN non-PKP / akun hilang).
- Helper yang kini mengunci mendapat parameter `kunci` (bawaan True, jadi pemanggil lama tidak berubah):
  - `cn_tertunda.hitung_untuk_nk` (kunci_faktur + FOR UPDATE OF sii);
  - `cn_tertunda.pulihkan_saat_void(periksa_saja=True)` (kunci_faktur);
  - `periksa_bisa_dinotakan` (CN_FAKTUR);
  - penguncian `CN_RETUR_FAKTUR` di posting.
- Pemeriksaan compare-and-set yang hanya bisa gagal saat balapan TETAP di penulis dan TIDAK menjadi blok
  pratinjau: `UPDATE 1` di apply/unapply, dan `catat_porsi` 409.

## 2. Pemetaan NK ↔ ND (U3d)
| NK | ND (U3d) | beda |
|---|---|---|
| rencana_posting_nk | rencana_posting | NK: porsi Dimuka (V312), pecahan Piutang/KRD, restock HANYA untuk retur ber-faktur (HPP asal) / WAC tanpa faktur |
| rencana_terapkan_nk | rencana_terapkan | NK: satu faktur, harus PENUH, tanpa jurnal (atribusi) |
| rencana_lepas_nk | rencana_lepas | NK: tanpa jurnal, ditolak bila periode tanggal penerapan tutup |
| rencana_refund_nk | rencana_kembali | arah terbalik (NK: Dr Piutang / Cr Kas, withdrawal) |
| rencana_batal_nk | rencana_batal | NK: draf = DIHAPUS; KRD ikut di-void; companion COGS-CN dibalik |
| baris_posting_nk / baris_refund_nk / baris_hpp_nk | baris_posting_nd / baris_refund_nd / rencana_hpp | — |

## 3. Kontrak respons pratinjau — FE TIDAK berubah, kecuali satu hal
Kunci sama: `can_proceed`, `blocks`, `before` / `after`, `journals`, `invoice`, `bank`, `payload`, `preview`.
`after` dihitung dari rencana (sisa = total − applied − refunded menurut rencana; saldo KRD; sisa faktur =
sisa_sebelum − porsi_AR).

**Satu perubahan:** `journals[].journal_number` kini `null` (pratinjau tak lagi memanggil penomoran); `source_type` menandai jenis
jurnal. Sama persis dengan ND U3d (nota_debit_rencana.py:60, diukur). Perlu dicek pembacanya di FE
(FRONTEND / ANDROID). Kalau ada yang menampilkan nomor jurnal pratinjau, perlu diputuskan.

## 4. Tahap (diusulkan: DUA unit, supaya tiap tinjauan kecil)
- **NK-RENCANA-A**: apply / unapply / refund. Tanpa inventaris, tanpa porsi tertunda. Kecil.
- **NK-RENCANA-B**: post / void. Berat: restock, HPP, KRD, DTL, Dimuka, companion.
Masing-masing: harness, rilis, izin pemilik sendiri-sendiri.

## 5. Harness (`scripts/ops/uji-nyata-nk-rencana-{a,b}.py`), pola U3d
- **kaos, TES E2E, satu transaksi luar yang SELALU di-rollback** (get_pool ditambal ke satu koneksi):
  1. Penulis LAMA (kode pohon utama) vs penulis BARU (worktree) pada fixture yang sama. Byte-identik untuk:
     - journal_entries dan journal_lines (akun, D/K, memo, urutan, nomor);
     - credit_notes;
     - credit_note_applications;
     - credit_note_refunds;
     - bank_transactions;
     - customer_deposits (KRD + nomor);
     - inventory_ledger;
     - document_tax_lines;
     - sales_invoice_items.allocated_amount dan credit_note_deferral_lines;
     - audit_logs (metadata).
     Pengecualian hanya uuid acak dan stempel waktu.
  2. Pratinjau == penulis: baris jurnal rencana == baris yang ditulis, `after` == keadaan sesudah tulis, bank / KRD
     sama.
  3. Matriks blok: setiap kode dipicu satu kali, lalu dibandingkan status dan detail penulis lama dengan blok
     pertama pratinjau baru. Fixture B mencakup retur ber-faktur (HPP asal), retur tanpa faktur (WAC), potongan
     dengan porsi Dimuka, kelebihan → KRD, PPN, serta void + KRD + companion.
- **Bukti pratinjau benar-benar baca-saja:**
  - `SET TRANSACTION READ ONLY` dari pemanggil, jadi tulis apa pun akan galat;
  - `pg_locks` advisory sesi = 0 selama pratinjau;
  - penghitung nomor tak bergeser.
- **grapgrap BACA-SAJA**: hanya pratinjau, semua NK yang memenuhi syarat, transaksi READ ONLY. Hasil: nol 500 dan
  nol galat tulis, ditambah ringkasan blok.
- **Kontrol MERAH** untuk setiap pemeriksaan (rencana disabotase → harus MERAH), plus MERAH_BOCOR dengan cleanup
  dan SISA 0. Net 0 diukur sesudah proses keluar (JEPRET terpisah).
- Tes unit pemindai: tak ada `pg_advisory` / `FOR UPDATE` / `get_next_journal_number` / INSERT / UPDATE di jalur
  pratinjau. Docstring dibuang lewat AST. Nama pembantu tidak generik.

## 6. Temuan sampingan (TIDAK diubah di unit ini; penulis harus byte-identik)
- void :3314 komentar "Mark original companion VOID" BASI; kodenya benar (hanya reversed_by_id/at, status TETAP POSTED) — diukur, bukan temuan.
- **Pratinjau sekarang sudah mengambil advisory lock** (CN_FAKTUR, INVOICE_FULFILL). Unit ini sekaligus menutupnya.

## 7. Pertanyaan ke MASTER
1. Setuju dipecah menjadi dua unit (A dulu)?
2. `journal_number: null` di pratinjau: setuju (preseden U3d)? Atau tanya FE dulu?

## 8. Tahap A — hasil (23 Okt 2026, cabang feat/nk-rencana-a)
- `services/nota_kredit_rencana.py`: `rencana_terapkan_nk` / `rencana_lepas_nk` / `rencana_refund_nk` (+ `baris_refund_nk`,
  `status_nk_sesudah` == trigger `update_credit_note_status` termasuk pembulatan BIGINT, `kredit_piutang_nk` == cabang 2
  compute_ar_outstanding) dan `pratinjau_aksi_nk`. Inti apply/unapply/refund memanggil penentu SESUDAH kunci lama; blok pertama = galat lama.
- Pratinjau: `conn.transaction(readonly=True)`; `_rencana_nk_aksi` + savepoint inti DIHAPUS.
- Beda yang disengaja: `journals[].journal_number = null` (FE resepAksiNk.tsx:34 jatuh ke "Jurnal"); baris jurnal pratinjau membawa `memo`;
  refund yang TERBLOK di status/bertaut tidak lagi menampilkan `bank` (dulu akun tetap dipecahkan) -> `bank: null` (FE: null diizinkan).
- Harness `scripts/ops/uji-nyata-nk-rencana-a.{py,sh}` (kaos, rollback): R refund rekening, K refund account_id (cermin reverse-lookup CoA),
  A terapkan, U lepas, B1–B5 blok. HIJAU 60 PASS rc 0: penulis lama == baru (jurnal, cermin bank, aplikasi, refund, kolom NK, audit),
  pratinjau lama (tulis+rollback) == baru (penentu) kecuali nomor jurnal, pratinjau == keadaan nyata sesudah tulis, nol tulis + nol kunci
  advisory baru + pencacah tetap selama pratinjau, NET 0. MERAH (after.remaining dimutasi) rc 1; MERAH_BOCOR rc 1, bersih SISA 0.
- BACA koneksi read-only: kaos 23 NK × 3 aksi = 69 pratinjau, 0 galat; grapgrap 0 NK (diukur) -> tak ada yang dipratinjau.
- Tes unit: test_nk_rencana_a (pemindai jalur pratinjau + kontrol merah pemindai, rumus status/BIGINT, pratinjau refund dengan koneksi
  tiruan yang menolak tulis); u10 / bertaut / potongan_pagar / u11 disesuaikan ke penentu. Terfokus 1025 passed.

## 9. Tahap B — hasil (23 Okt 2026, cabang feat/nk-rencana-b)
- `rencana_posting_nk` / `rencana_batal_nk` (+ `baris_posting_nk`, `baris_hpp_nk`, `_rencana_restock_nk` KHUSUS pratinjau,
  `_jurnal_balik_nk`, `pratinjau_post_void_nk`). Inti terbit/batal memanggil penentu sesudah kunci lama; akun, porsi Dimuka, pecahan
  piutang/KRD diambil dari rencana. Restock TULIS tetap di loop penulis (urutan inbound/WAC per baris; satu kueri keluar-faktur per baris).
- `kunci` (bawaan True) di `cn_tertunda.hitung_untuk_nk`, `cn_tertunda.pulihkan_saat_void`, `periksa_bisa_dinotakan`; pratinjau kunci=False.
- Pratinjau lama (`_rencana_nk`, `_jurnal_tx_ini_nk`, `_BatalkanPratinjauNk`, savepoint inti) DIHAPUS.
- Beda yang disengaja: journal_number null; pratinjau terbit KRD baru `deposit_number` null; pesan blok "bukan draf" kini == galat penulis
  (pratinjau LAMA: "berstatus terbit tidak bisa diterbitkan." vs penulis "berstatus Terbit — tidak bisa diterbitkan." -- cacat lama,
  harness B1 pohon lama merah by design).
- Harness `scripts/ops/uji-nyata-nk-rencana-b.sh` (kaos rollback): P1 NK bebas, P2 KRD sebagian (INV-2609-0122), P3 retur HPP asal + KRD
  (INV-2609-0140 lunas), P4 retur WAC tanpa faktur, V1–V4 batal (pembalik + HPP + KRD void), V5 batal draf, B1/B2 blok. HIJAU 82 PASS rc 0:
  penulis lama == baru (jurnal, kartu stok, KRD, DTL, kolom NK, alokasi + porsi), pratinjau lama == baru, pratinjau == keadaan nyata,
  nol tulis + nol kunci advisory baru + pencacah tetap, NET 0. MERAH rc 1; MERAH_BOCOR rc 1 SISA 0. Putaran 1 menangkap KRD sesudah batal
  (sisa 0) -> diperbaiki.
- BACA read-only: kaos 23 NK × 2 = 46 pratinjau 0 galat; grapgrap-manado 2 NK × 2 = 4 pratinjau 0 galat.
- Tes: test_nk_rencana_b (kunci=False vs True + kontrol merah), test_u5d_nk_pratinjau ditulis ulang untuk penentu, 8 tes pemindai
  dipindah ke penentu. Terfokus 1007 passed. Temuan sampingan: impor `compute_deposit_remaining` relatif services salah -> dibetulkan ke routers.
