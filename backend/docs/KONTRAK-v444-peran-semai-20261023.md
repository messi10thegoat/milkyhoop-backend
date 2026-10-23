# DESAIN V444 — semai peran akun yang hilang + penjaga (23 Okt 2026, BACKEND)

## Temuan (diukur, 6 tenant nyata di "Tenant")
- Peran yang di-resolve kode tapi TIDAK disemai `seed_default_account_roles` (onboarding_service:142 untuk tenant BARU):
  EMPLOYEE_ADVANCE (V281, hanya grapgrap/kaos), BANK_ADJUSTMENT_GAIN/LOSS (V274, hanya grapgrap), REVENUE_SALES_SERVICE (V260, 4 tenant).
  -> setiap tenant baru lahir tanpa keempatnya.
- Celah per tenant:
  - grapgrap-manado: LENGKAP (tak disentuh).
  - kaos: BANK_ADJUSTMENT_GAIN/LOSS.
  - adhita / subbidel: BANK_ADJUSTMENT_GAIN/LOSS + EMPLOYEE_ADVANCE.
  - anthonius / ponte: BANK_ADJUSTMENT_GAIN/LOSS + EMPLOYEE_ADVANCE + REVENUE_SALES_SERVICE.
- Dampak hari ini: 422 jelas, bukan 500. `/bank-accounts/{id}/adjust` -> 400 "Akun penyesuaian saldo belum diatur"; kasbon -> 422
  ACCOUNT_DEFAULT_UNMAPPED (tangkap lokal + handler global main.py:314); jasa -> fallback ke pendapatan barang + WARNING (pendapatan jasa
  tercampur di 4-10100 untuk anthonius/ponte).
- Akun calon di CoA (diukur): SEMUA tenant punya 4-90000 Pendapatan Lain-lain (OTHER_INCOME), 5-20900 Beban Lain-lain (EXPENSE),
  4-10150 Pendapatan Jasa (REVENUE). 1-10450 Piutang Karyawan (Kasbon) hanya grapgrap/kaos; `seed_default_coa` TIDAK membuatnya.

## V444 (migrasi; izin pemilik LANGSUNG — aturan akun)
1. Pola V452 (urutan migrasi tak berpengaruh): tambal definisi LIVE lewat `pg_get_functiondef` + ganti anchor, gagal-keras bila anchor tak
   ada, lewati bila sudah memuat baris itu.
   - `seed_default_account_roles`: + BANK_ADJUSTMENT_GAIN '4-90000', BANK_ADJUSTMENT_LOSS '5-20900', REVENUE_SALES_SERVICE '4-10150',
     EMPLOYEE_ADVANCE '1-10450' (akun = pilihan pemilik untuk grapgrap di V274/V281/V260, dijadikan bawaan generik).
   - `seed_default_coa`: + '1-10450' Piutang Karyawan (Kasbon) ASSET DEBIT, induk 1-10000, sama dengan baris V281 grapgrap.
2. Backfill tenant lama (idempoten, ON CONFLICT DO NOTHING; grapgrap-manado tak berubah):
   - buat 1-10450 di tenant yang belum punya (4 tenant kosong; pola V281);
   - `SELECT seed_default_account_roles(id) FROM "Tenant"` -> hanya mengisi celah.
3. Penjaga pasca-migrasi di blok DO (gagal-keras):
   - setiap tenant punya keempat peran;
   - akun = kode yang diharapkan, bukan header;
   - grapgrap-manado: account_roles md5 sebelum == sesudah.
4. ROLLBACK: hapus peran yang ditulis V444 (penanda `notes` diawali 'V444'); hapus 1-10450 buatan V444 hanya bila tanpa journal_lines
   (bila ada -> gagal-keras, jangan hapus akun terpakai); kembalikan dua fungsi lewat anchor balik.
Nol jurnal disentuh (Law 18/19 aman: hanya CoA baru + pemetaan).

## Penjaga kode (cabang yang sama, tanpa migrasi)
- Tes unit `test_peran_semai_lengkap`: SETIAP `AccountRole.X` yang di-resolve kode tanpa fallback harus ada di baris VALUES fungsi semai.
  Sumber = teks fungsi hasil tambalan terakhir di migrations (V444 menuliskan daftar lengkap ke berkas teks pembanding
  `backend/migrations/peran_semai.txt` agar tes tak perlu DB). Kontrol merah: peran baru tak disemai -> MERAH.
- Peringatan saat startup (lifespan, SATU kueri): tenant × peran wajib yang tak terpetakan -> `logger.warning`, tidak memblok.
  Daftar peran wajib = konstanta yang sama dengan tes.

## Uji
- Scratch DB salinan skema: terapkan -> penjaga hijau -> rollback -> terapkan ulang.
- Harness BACA: matriks celah sebelum (6 tenant) -> sesudah (nol celah); md5 account_roles grapgrap tetap.
- Nyata kaos (rollback): `/bank-accounts/{id}/adjust` dulu 400, sesudah V444 jurnal Pendapatan/Beban Lain-lain terbentuk.

## Pertanyaan
1. Akun bawaan (4-90000 / 5-20900 / 4-10150 / 1-10450) untuk SEMUA tenant = pilihan pemilik di grapgrap -> setuju dijadikan bawaan?
   (NetSuite/QBO: selisih penyesuaian bank = Other Income/Expense — sejalan.)
2. Nomor V444 (blok BACKEND; V442 ditahan pemilik). Pola anchor = aman dari urutan.

## Hasil (23 Okt 2026, cabang feat/v444-peran-semai)
- MASTER tambahan: (a) anthonius/ponte jurnal = 0 (diukur) -> tak ada riwayat lewat fallback 4-10100; (b) cek startup = tugas LATAR
  (restart tak menunggu), kueri 4 ms di prod; (c) penjaga md5 grapgrap-manado mencakup account_roles + chart_of_accounts.
- Backfill memakai seed_default_account_roles sendiri (menguji fungsi tertambal) + penjaga: baris baru == celah terukur (diukur: tak ada
  peran semai LAIN yang hilang di tenant mana pun) -> 16 baris (kaos 2, adhita 3, subbidel 3, anthonius 4, ponte 4) + 4 akun 1-10450.
- Scratch `scripts/ops/uji-scratch-v444.sh`: MERAH anchor AR_TRADE -> gagal keras + fungsi pulih identik; terapan -> celah 0, diff fungsi
  hanya baris dimaksud, grapgrap md5 sama; tenant BARU dummy dapat 4 peran + 1-10450; ulang = no-op; ROLLBACK memulihkan kedua fungsi
  identik + celah kembali persis; terapan ulang sesudah rollback lolos; DB scratch dihapus (sisa 0). RINGKAS gagal=0.
- Kode: role_resolver.PERAN_SEMAI + laporkan_peran_tak_terpetakan (lifespan, create_task); tes test_peran_semai_lengkap (setiap
  AccountRole.X yang dirujuk kode ⊆ PERAN_SEMAI ⊆ baris semai di migrasi ⊆ katalog; kontrol merah; peringatan tak pernah jatuh).
  Terfokus 189 passed. Batas: peran yang dirujuk sebagai STRING literal (bukan AccountRole.X) tak terpindai.
