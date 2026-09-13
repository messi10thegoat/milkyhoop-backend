# TEMUAN — drift WAC −47.994,31 DAN dua penjaga yang mati (13 Sep 2026)

**Status:** TERBUKA, **melingkupi saja**. **Nol perbaikan, nol pengampunan, nol
sentuhan data.** Yang dilakukan hanya membaca.

Dua hal ditemukan bersama, dan yang kedua lebih besar daripada yang pertama.

---

## BAGIAN 1 — drift terjelaskan TUNTAS, sampai rupiah

`check_15` melaporkan `kaos-biru-konveksi` GL `1-10600` **8.249.545,00** vs
nilai persediaan **8.297.539,31**, drift **−47.994,31**, toleransi 0,43,
`FAIL_NON_EXEMPT`, tabel pengampunan WAC **kosong**.

**Dua sebab, arah berlawanan, nol sisa:**

```
−128.000,00   ledger ADA, GL TIDAK
              inventory_ledger OPENING_BALANCE · "Cm20s - 2" (Cotton Combed
              Maroon 20s) · 1 unit @128.000 · journal_id NULL · 4 Sep
              source_id f06b251c-… TIDAK COCOK dengan baris mana pun di
              SELURUH tabel ber-id uuid di skema (dicari menyeluruh, 0 baris).
              Nilai persediaan lahir TANPA jurnal di mana pun.

+ 80.006,00   GL ADA, ledger TIDAK — semuanya source_type='BILL', 2 Sep
              PJ-2609-0008  10.001    ZSEARCHBILL-1   grand_total 0,00
              PJ-2609-0009  10.002    ZSEARCHBILL-2   grand_total 0,00
              PJ-2609-0010  10.003    ZSEARCHBILL-3   grand_total 0,00
              PJ-2609-0021  50.000    BILL-2609-0008  grand_total 50.000
──────────────
−47.994,00  (+ sisa pembulatan WAC 0,31 = −47.994,31)
```

### ⚠️ KOREKSI (13 Sep, sore) — DUA mekanismeku KELIRU, keduanya kucabut

Versi pertama dokumen ini menyalahkan **ketiadaan produk** pada baris tagihan,
mengutip komentar kepala `bills_service.py:42` (*"INVENTORY_MERCHANDISE:
default Dr account on bill"*). **Komentar itu sudah tidak dipatuhi kodenya
sendiri sejak 17 Juni** (`331f038c`, "per-line GL - goods->inventory,
service->expense"). Iron Law 34 **di dalam berkas yang sedang kubaca**: komentar
menjanjikan lebih daripada kode, dan ia nyaris jadi temuan yang kuterbitkan.

Dugaan keduaku — bahwa penentunya **tautan `production_subcontracts` yang
hilang** — juga **KELIRU**. Tautan itu memang hanya lahir dari jalur produksi
(`production.py:938`), tapi ia bukan titik putusan untuk kasus ini.

**Yang sebenarnya, dan ia TERTULIS SEBAGAI DISENGAJA** (blok ~2924, "Defect #D2
fix: PER-LINE debit resolution by item type", **terverifikasi HIDUP di dalam
kontainer**):

```
Goods (item_type=goods AND track_inventory) -> CoA persediaan produk   + baris ledger
Service / non-tracked                          -> CoA COGS/beban produk   TANPA baris ledger
Free-text / no-product lines                   -> DEFAULT (INVENTORY_MERCHANDISE)
                                                  "same as legacy behavior for
                                                   un-itemised cost"
```

Keempat tagihan itu **baris ketikan bebas**: `product_id` NULL, deskripsi kosong
atau `"maklun uji"`. Mereka jatuh ke cabang **ketiga** — yang memang dirancang
jatuh ke persediaan.

**Akibatnya tiga, dan ketiganya mengubah rumusan awalku:**
1. Ini **keputusan rancangan yang tertulis**, bukan kelalaian → **putusan
   pemilik**: baris ketikan bebas di tagihan pembelian seharusnya masuk **beban**
   atau **persediaan**? Bukan tambalan kita.
2. Jalur `item_type=service` **sudah benar sejak Juni**. Jangan perbaiki yang
   tak rusak.
3. Blok #D2 itu **mendiagnosis persis kelas drift ini** — *"mis-booked as
   inventory asset with NO inventory_ledger row -> GL(1-10600) > ledger drift by
   construction"* — dan menutup dua dari tiga cabangnya. **Seseorang sudah
   sampai di sini sebelum aku.**

⚠️ **Dan ini kesalahan proses yang SAMA yang kutulis sendiri di commit Unit 2
dua belas jam sebelumnya**: membangun penjelasan sebelum mencari diagnosis yang
SUDAH ADA. Kutulis aturannya, lalu kulanggar di temuan berikutnya.

### Sebabnya: AKUN SALAH, bukan pasangan atomik yang putus

Ini fork yang penting, dan pemisahnya **sempurna, nol pengecualian**:

| | baris item | item ber-produk | baris ledger |
|---|---|---|---|
| keempat tagihan di atas | 1 | **0** | **0** |
| enam tagihan lain yang mendebit 1-10600 | 1–2 | 1–2 | 1–2 |

Penulis `inventory_ledger` **bekerja benar di seluruh sepuluh kasus**: ada
produk → ada baris ledger; tak ada produk → tak ada baris. Yang salah adalah
**pemetaan akun**: keempat jurnal itu berbentuk dua baris
`1-10600 (ASSET) D / 2-10100 (PAYABLE) K`, memo **"Pembelian dari UJI Maklun"**
— pembelian **jasa/maklun**. Pembelian jasa **tak boleh** mendebit akun
persediaan; ia milik beban atau WIP.

⚠️ **Nominal 10.001/10.002/10.003 berurutan beda satu rupiah = tangan manusia
yang sedang menguji.** Tapi **data uji tidak menjawab pertanyaan jalur kode**:
kalau jalur tagihan bisa mendebit persediaan untuk baris tanpa produk, ia akan
melakukannya lagi pada transaksi sungguhan. **Sebab yang ditemukan lewat data
uji tetap sebab.** `BILL-2609-0008` (50.000) bahkan tidak berpenanda uji.

### Dua dugaanku yang DICABUT

1. **Bukan** selisih pergerakan-vs-WAC — terukur hanya **0,31** atas 9 produk.
2. **Bukan** `BRG-0065` / ketidakseragaman biaya `PRODUCTION_OUTPUT`
   (unit_cost 41.562 vs average_cost 49.000, selisih 714.048 muncul dua kali).
   **`PRODUCTION_OUTPUT` rukun 0,00** meski 18 baris ledger vs 6 baris GL.

Keduanya masuk akal; keduanya keliru. Yang memutuskan **aritmetika yang tutup
sampai rupiah**, bukan kemasukakalan.

---

## BAGIAN 2 — DUA penjaga mati, dan mesin yang mematikannya

### Dua fungsi yang dirujuk TIDAK ADA di `pg_proc`

```
compute_inventory_adjustments   baris 451 -> check_9_inventory_value
compute_ap_adjustments          baris 385 -> check_7_ap_invariant
```

Keduanya **nol** di `pg_proc` pada signature mana pun.
`compute_inventory_adjustments` hanya muncul **di berkas skrip itu sendiri** —
tak ada di migrasi mana pun, tak ada di Python. **Ia tak pernah ditulis**, jadi
`check_9` mati sejak hari ia ditambahkan, bukan rusak belakangan.

### Mesinnya — dan ini yang harus diperbaiki, bukan dua fungsi hilang

```
psql_cmd()  baris 59:  docker exec … psql … 2>/dev/null   <- stderr DIBUANG
            baris 64:  psql_lines() sama

lalu TUJUH situs:  || [ -z "$x" ]  ->  CHK_PASS=1         <- KOSONG = LULUS
128  check_2_hash_chain          454  check_9_inventory_value
388  check_7_ap_invariant        479  check_15_inventory_wac
420  check_14_ar_reconciliation  506  check_16_deferred_revenue
674  check_13_status_desync
```

**Gabungan keduanya: setiap kegagalan kueri — apa pun sebabnya — menjadi PASS.**
Terbukti, bukan disimpulkan: kueri `check_9` dijalankan apa adanya →
`ERROR … does not exist`, `EXIT=1`, stdout kosong.

Jadi **"Passed: 27"** di laporan harian memuat **setidaknya dua** pemeriksaan
yang tak pernah mengukur apa pun — dan salah satunya (`check_7`) menjaga
**hutang usaha**.

Lima sisanya **hidup hari ini** karena fungsinya kebetulan ada. Cacatnya
**laten**: satu galat SQL, satu rename kolom, satu perubahan signature, dan
mereka berpindah ke "lulus" **tanpa suara**.

### ⚠️ Baris 128 ADALAH MILIKKU, mendarat 13 Sep pagi

`check_2_hash_chain` versi patok garis-dasar memuat `|| [ -z "$verdict" ]`.
Aku menyalinnya dari `check_14`/`check_15` **sambil merasa benar karena tidak
mengarang varian baru** — dan justru itu yang memindahkan cacatnya, ke penjaga
yang dipasang **untuk memperbaiki penjaga**.

**Mekanisme penyebaran yang layak dinamai: meniru dengan TELITI menyebarkan
cacat lebih andal daripada mengarang varian baru, karena peniru merasa paling
aman justru saat ia paling menyalin. Konsistensi terasa seperti disiplin.**

### Obat — urutannya menentukan, dan urutan yang salah membatalkan kerja kemarin

1. **KONTROL POSITIF SELURUH PAPAN DULU.** Satu jalan dengan satu fungsi
   sengaja di-rename **harus memerahkan** pemeriksaannya. Tanpa ini kita cuma
   percaya — dan "cuma percaya" adalah keadaan hari ini.
2. **Baru** periksa tiap kueri: bisakah ia **sah-kosong**? Yang sah-kosong
   dibuat **menyatakan** nilainya eksplisit (`'0'` / `'PASS'`).
3. **Baru** balik saklarnya ke **kosong = GAGAL**, dan **jangan buang stderr**.

⚠️ **Membalik saklar tanpa langkah 2 akan memerahkan pemeriksaan yang SAH
kosong** — `verify_chain_integrity_all()` tak mengembalikan baris untuk tenant
tanpa jurnal POSTED, dan itu bukan kegagalan. Papan merah semua besok pagi
mengajari pemilik mengabaikan alarm — **persis kerusakan yang patok
garis-dasar baru saja cegah.** Penjaga yang diam dan penjaga yang berteriak
terus sama-sama tak dibaca.

---

## Yang TIDAK boleh dilakukan atas temuan ini

- **Jangan patok/ampuni drift WAC.** Sebabnya kini dipahami, tapi memperbaiki
  data = menyunting pembukuan = putusan pemilik.
- **Jangan tutup tiket penjaga dengan dua tambalan fungsi.** Gejalanya dua;
  **mesinnya satu**.
- **Selisih 3 juta `check_9` vs `check_15` atas 1-10600 jangan diputuskan
  sekarang** (5.174.576 vs 8.249.545): `check_9` tak pernah berjalan, jadi
  angkanya **artefak kueri yang mati di tengah**, bukan hasil pemeriksaan.
  Bandingkan ulang **sesudah** ia hidup.

---

# SUSULAN (13 Sep, sore) — TIGA penjaga mati, dan log yang berbohong

## Penjaga ketiga: `check_13_status_desync` — SQL cacat sejak lahir

Bukan fungsi hilang; **kuerinya sendiri tak bisa di-parse**:

```sql
WHERE b.tenant_id =            -- tak ada nilai
  AND b.accounting_status != POSTED    -- literal tanpa kutip
  AND je.source_type = BILL            -- literal tanpa kutip
```

Dijalankan apa adanya: `ERROR: syntax error at or near "AND"`.
Identik di disk DAN di blob git (`git diff` kosong). Lahir cacat di
`0bdebe86 "feat(health): add Check 13 — status desync detection"`.
Ia salah satu dari tujuh situs `-z` (baris 674), jadi galatnya jadi **LULUS**.

**Jadi tiga, bukan dua:** `check_7` (AP), `check_9` (nilai persediaan),
`check_13` (status desync). Ketiganya dihitung di **"Passed: 27"**.

## KONTROL POSITIF — sisi merah TERBUKTI di kode hari ini

Fungsi DB TIDAK disentuh. Yang dimutasi **salinan skripnya**, satu nama fungsi
disalah-ketik — stimulus yang sama (kueri gagal → kosong), nol risiko ke DB:

```
verify_chain_integrity_all -> verify_chain_integrity_allX   (3 rujukan)
hasil: Passed 27 · Critical 0 · baris "Hash Chain" muncul 0 kali
```

**Identik dengan jalan yang sehat.** Pemeriksaan yang fungsinya tak ada sama
sekali melaporkan LULUS, diam-diam. Inilah sisi merah yang wajib dibalik oleh
perbaikan: sesudah diperbaiki, mutasi yang sama HARUS memerahkan check 2.

## Log yang mengklaim kiriman yang mungkin tak pernah terjadi

```
1029  send_discord "$DISCORD_MSG"      -> curl HANYA jika WEBHOOK_URL tak kosong
1032  log "Discord message sent: ..."  -> DI LUAR penjaga, SELALU tercetak
```

Baris log itu **bukan bukti kiriman**. Siapa pun yang membaca log ini tak bisa
membedakan "alarm terkirim" dari "alarm tak pernah keluar" — termasuk saat
webhook mati. Aku sendiri sempat menuduh diriku mengirimi pemilik alarm uji
berdasarkan baris ini; **tidak ada yang terkirim**. Gejala ujung rantai,
sekali lagi.

## EMPAT idiom penanganan-kosong di SATU berkas

```
[ "$x" = "0" ]                      -> kosong jatuh ke ELSE = GAGAL (benar)  check_1, check_3
[ "$x" = "0" ] || [ -z "$x" ]       -> kosong = LULUS (cacat)                7 situs
${vendor_drift:-0} lalu -gt 0       -> kosong dianggap 0 = LULUS, numerik     check_11
verdict = PASS/PASS_EXEMPT || -z    -> kosong = LULUS (cacat)                 4 situs
```

`check_1`/`check_3` **sudah benar**. Jadi perbaikannya bukan mengarang
konvensi baru: **samakan tujuh situs itu dengan yang sudah benar di berkas yang
sama.**

## Yang SAH-kosong — terukur, bukan diandaikan

Loop tenant = tenant ber-jurnal POSTED: hari ini **dua**
(`grapgrap-manado`, `kaos-biru-konveksi`). Keempat `verify_*_all` mengembalikan
baris untuk keduanya. **Jadi hari ini TAK ADA pemeriksaan yang sah-kosong** —
setiap kosong yang bisa muncul sekarang adalah kegagalan.

⚠️ Tapi "hari ini" menanggung beban: tenant yang semua jurnalnya DRAFT tak
masuk loop, dan `verify_chain_integrity_all()` juga hanya memancarkan baris
untuk tenant ber-POSTED. **Kedua syarat itu kebetulan berimpit**, jadi
keamanan hari ini adalah kebetulan, bukan jaminan. Karena itu tiap pemeriksaan
harus **MENYATAKAN** nolnya (`0`/`PASS`), bukan bersandar pada keimpitan itu.

`check_16` mengembalikan **empat** tenant sementara loop punya dua
(`adhita-ariyani`, `subbidel-kolsani` tak pernah ditanya) — celah cakupan yang
**hampa hari ini**: keduanya nol jurnal, nol baris ledger, hanya punya CoA.

---

# SUSULAN 2 (13 Sep) — AUDIT KONSTITUSI: klaim perlindungan yang tak ada

Dipicu permintaan pemilik agar BE memuat `/milkyhoop-ironlaws`. Dimuat, lalu
tiap klaim pagar diverifikasi ke `pg_trigger` / `pg_index` / `pg_constraint` —
bukan ke changelog. Law 34 menuntut ini; dokumen ini hasilnya.

## Yang TERBUKTI TIDAK ADA

| Hukum | Klaim | Kenyataan |
|---|---|---|
| **Law 2** | *"stock-adjustment void path was the **lone offender**"* | **LIMA situs lain** masih membalik status ke VOID sampai 12 Sep: `expenses.py`, `bank_transfers.py`, `credit_notes.py` (×2), `vendor_credits.py`. Ditutup `84af5e59`. |
| **Law 19** | 5 trigger pembekuan nominal: `trg_invoice_freeze`, `trg_bill_freeze`, `trg_expense_freeze`, `trg_receive_payment_freeze`, `trg_bill_payment_freeze` | **NOL dari lima ADA**, dan **tak ada padanannya di bawah nama lain**. Sembilan trigger di kelima tabel itu: 2 stempel `updated_at`, 2 validator enum status, 2 pencatat penghapusan, 1 pembaru bill, 1 penjaga void jurnal. **Tak satu pun membekukan nominal.** |
| **Law 22** | `UNIQUE INDEX idx_journal_sequence_uniq (tenant_id, chain_sequence)` | **TIDAK ADA.** Yang ada `idx_je_chain_seq`, **tidak unik**. |
| **Law 26** | trigger `trg_prevent_reverse_of_reversal` | **TIDAK ADA** di bawah nama apa pun pada `journal_entries` (daftar lengkap 6 trigger diperiksa). Indeksnya (`idx_je_single_reversal`, unik) **BENAR ADA**. |
| **Law 30** | `chk_rp_obligation` + `chk_bp_obligation` (dicetak sebagai SQL siap pakai) | **TIDAK ADA.** 11 CHECK di ketiga tabel settlement **semuanya enum status/metode**. Tak satu pun menuntut rujukan obligasi. |

## Yang BUKAN klaim palsu — drift NAMA, pagarnya ADA

Dicatat supaya laporan ini adil dan tak memicu perbaikan yang tak perlu:

| Diklaim | Nama sebenarnya | Status |
|---|---|---|
| `trg_journal_immutable` (Law 2) | `trg_prevent_posted_journal_update` / `_delete` | **ADA, bekerja** |
| `trg_check_period` (Law 5) | `trg_prevent_closed_period_journal` | **ADA, bekerja** |

⚠️ Aku nyaris melaporkan keduanya sebagai pagar hilang. Itu akan jadi kesalahan
dengan bentuk yang sama seperti menyimpulkan perilaku dari komentar kode:
**menyimpulkan dari NAMA, bukan dari perilaku.**

## Kenapa Law 19 yang paling berat

Law 19 adalah hukum yang menjanjikan **nominal dokumen sumber beku sesudah
jurnalnya POSTED**. Ia dikutip sebagai alasan "edit = reversal + dokumen baru".
Kalau penegakannya nol, maka `UPDATE bills SET grand_total = ...` atas tagihan
yang sudah POSTED **berhasil tanpa perlawanan**, dan jurnalnya tak ikut berubah
— dokumen dan buku besar berpisah diam-diam.

Ini persis kelas yang **Law 13 catatan arsitektural** sudah namai:

> *"invariant yang bisa dinyatakan sebagai predikat atas baris DB HARUS
> ditegakkan di DB. Yang ditegakkan konvensi kode akan bocor."*

Law 19, 22, 26, 30 semuanya adalah predikat atas baris DB. Semuanya diklaim
ditegakkan DB. Tak satu pun benar-benar ditegakkan di sana.

## Instans BARU untuk Law 33 (mekanisme ke-8)

`check_7`, `check_9`, `check_13` **bukan** gate yang memeriksa hal salah, bukan
gate yang mustahil menyala, bukan prasyarat yang tak dijalankan. Mereka:

> **gate yang GALATNYA DIBACA SEBAGAI LULUS** — `psql_cmd` membuang stderr,
> lalu `[ -z "$x" ]` memaknai kosong sebagai lulus. Alatnya **tak pernah
> berjalan sama sekali**, dan diamnya terbaca sebagai sehat.

Instans 1–7 semuanya soal alat yang **berjalan** lalu diam. Ini kebalikannya.
