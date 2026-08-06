# Alat verifikasi mendokumentasikan langkah yang tak dilakukannya

**Tanggal:** 2026-08-06 **Severity:** P0 untuk kepercayaan verdict (bukan bug produk)
**Status:** **DIPERBAIKI** — `run_all.sh` kini menjalankan prasyaratnya sendiri + gate keras
**Kelas:** Law 33 **instance kelima** — mekanisme BARU (lihat bawah)

## A. Header menjanjikan restore; skrip nol memanggilnya

`run_all.sh` baris 10-13 (sebelum perbaikan):
```
# PROCEDURE (run BEFORE this script — kept separate so this stays a pure flow test):
#   1. Restore milkydb from the preharness snapshot ...
#   2. Verify pristine: "Tenant"=0, schema_migrations=214 ...
```
`grep -n restore_preharness run_all.sh` → **nol hasil**. Tidak ada pula gate yang memeriksa apakah
prasyarat itu benar-benar dijalankan.

**Ongkos terbukti:** harness dijalankan di atas DB walkthrough (13 jurnal) → step -1 gagal dengan
`AP drift 7.000.000`, `BANK_GAP 7.002.500`, `ERROR: division by zero`. Gejalanya **persis seperti
regresi produk akibat V221**. Nyaris dilaporkan begitu. Yang menyelamatkan hanya kecurigaan pada
bentuk angkanya (7.000.000 = 2 × 3.500.000 → bau data lama, bukan bau migrasi).

## B. `restore_preharness` MEMUNDURKAN SKEMA → harness menguji skema lama, diam-diam

Snapshot preharness bertanggal **26 Juli** dan memuat **214 tracked, tertinggi V220**. Restore
mengembalikan seluruh isi DB **termasuk `schema_migrations`**, sehingga migrasi yang lahir setelah
tanggal itu **lenyap**:

```
sesudah restore: PRIMARY KEY (key)          <- V221 hilang
                 schema_migrations = 214    <- turun dari 215
```

Tanpa `migrate.sh apply` di antaranya, setiap `run_all.sh` menguji **skema 26 Juli**, bukan skema
master — sambil melaporkan **hijau**.

### Seberapa besar? — TERBATAS, dan itu penting

Snapshot **sudah memuat V218/V219/V220** (diverifikasi dengan membaca isi dump, bukan menyimpulkan
dari angka). Jadi seluruh run sebelum sesi ini **menguji skema yang benar**; **V221 adalah migrasi
pertama yang lahir setelah tanggal snapshot**, sehingga yang pertama terkena.

**Nol klaim "run_all hijau" di sesi lalu yang perlu ditinjau.** Ini jebakan baru, bukan kerusakan
retroaktif.

## Perbaikan

`run_all.sh` kini menjalankan **PREFLIGHT** sendiri:
1. `restore_preharness.sh` — pristine
2. `migrate.sh apply` — **menjalankan rantai migrasi** di atas snapshot
3. `migrate.sh verify` — **gate keras**, `exit 9` bila ada `V*.sql` on-disk yang belum tracked

Header ditulis ulang agar menyatakan apa yang benar-benar dilakukan, plus catatan sejarah singkat.

### Kenapa restore→apply, BUKAN "bikin snapshot baru ber-skema master"
Snapshot ber-skema master menghapus properti terpenting harness: ia **menjalankan rantai migrasi
setiap run**. Snapshot baru hanya menguji **skema akhir** tanpa pernah menguji **jalan menuju ke
sana** — migrasi yang tak idempoten atau rusak tak akan pernah ketahuan. Godaan "bikin snapshot baru
biar tak perlu apply" **harus ditolak**; alasannya ditulis di komentar skrip supaya tak hilang.

### Bentuk assert — koreksi terhadap rumusan awal
Rumusan "assert `count(tracked) == count(file VNNN)`" **tidak bisa dipakai**: `schema_migrations`
memuat entri pembukuan non-VNNN (`GAP_PATCH`, `STEP0_STUB`), sehingga jumlahnya **tidak pernah** sama
(213 file vs 215 tracked) dan assert bentuk itu akan gagal-palsu selamanya — ironisnya menciptakan
gate rusak baru saat memperbaiki gate rusak lama.

Yang benar: **nol `V*.sql` on-disk yang belum tracked**, yaitu persis yang `migrate.sh verify`
periksa (rc≠0 saat drift). Memakai alat yang sudah ada juga menghindari dua definisi "benar" yang
bisa saling bertentangan.

## Uji dua arah (Law 33)

| Arah | Cara | Hasil |
|---|---|---|
| **MERAH** | `PREFLIGHT_SKIP_APPLY=1` — mensimulasikan bug nyata (restore memundurkan skema, apply tak jalan) | gate 3/3 menangkap: `DRIFT: on-disk but NOT tracked : V221...` → **exit 9**, flow **tak pernah jalan**, nol "ALL PASS" |
| **HIJAU** | jalan normal | **exit 0**, PREFLIGHT 1-3 OK, `✅ ALL STEPS PASS`, closing invariant bersih |

Closing invariant HIJAU: `GROSS_PROFIT 1.500.000` · `TRIAL_BALANCE 47.000.000` · `BANK_DELTA
+1.500.000` · `COGS_SALES 3.500.000` · `REVENUE_SALES_GOODS -5.000.000` · AR/AP/VAT/HASH_CHAIN 0 —
identik dengan run sebelumnya. Durasi 114s (naik dari 37s karena preflight kini nyata).

`PREFLIGHT_SKIP_APPLY` adalah **hook uji-merah**, ditandai jelas di kode. `SKIP_PREFLIGHT=1` tersedia
untuk rerun cepat di DB yang sudah disiapkan tangan, dan mencetak peringatan bahwa **hasilnya tidak
sah sebagai verdict**.

## Kenapa ini instance Law 33 yang BARU

Empat instance sebelumnya adalah **gate yang bisu** — alatnya berjalan tapi tak bisa berbicara
(BANK_GAP memeriksa hal salah; zlib-grep mustahil menyala; `rsync --delete` bisu karena mode;
guard drift membaca header).

Instance kelima berbeda: **gate-nya berfungsi sempurna, tapi PRASYARATNYA tak dijalankan.** Hasil
hijau maupun merah sama-sama **tidak sah** — bukan karena alat gagal mengukur, melainkan karena yang
diukur bukan objek yang dimaksud. Keheningan bukan masalahnya; **kesahihan** yang hilang.

**Aturan turunan:** gate tidak cukup bisa berbicara — ia juga harus **memastikan ia berbicara tentang
objek yang benar**. Prasyarat yang tak ditegakkan mesin akan dilupakan manusia.

## Silang-rujuk
Juga instance dari pola **"dokumen mencatat niat, bukan keadaan"** —
lihat `2026-08-06-pattern-engine-built-wiring-unfinished.md`. Di situ engine dibangun tapi tak
disambungkan; di sini prosedur ditulis tapi tak dijalankan. Akar yang sama: **niat terdokumentasi
diperlakukan seolah sudah menjadi keadaan.**
