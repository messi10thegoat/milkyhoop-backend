# USULAN (belum diimplement): trigger DB penegak saldo baris-vs-header

**Tanggal:** 2026-08-06 **Status:** USULAN — **JANGAN diimplement tanpa GO owner**
**Menutup:** `DOCS/issues/2026-08-06-guard-line-header-scope.md`

## Masalah dalam satu kalimat

`je_balanced CHECK` hanya membandingkan **header vs header**; tak ada apa pun yang memaksa
`SUM(journal_lines) = journal_entries.total_*`, sehingga **94 jalur posting** di 32 file
masing-masing bisa memposting buku timpang, dan nol kernel bersama untuk menambalnya sekali.

## Kenapa trigger, bukan konvensi kode

Argumen identik dengan UNIQUE index V218: **konvensi bisa dilanggar; constraint tidak.**

- Guard di kode = 94 tempat harus ingat, dan jalur ke-95 (fitur berikutnya) pasti lupa.
- Guard di DB = 1 tempat, berlaku untuk semua jalur **termasuk yang belum ditulis**, dan juga
  untuk mutasi manual/skrip/migrasi yang tak lewat gateway sama sekali.
- Law 4 adalah *konstitusi*; menegakkannya di lapisan aplikasi membuatnya opsional secara de-facto.

## Rancangan

```sql
-- V2xx__enforce_line_header_balance.sql   (nomor: fetch-before-apply, lihat
--                                          memory migration-number-reservation)

CREATE OR REPLACE FUNCTION enforce_line_header_balance() RETURNS TRIGGER AS $$
DECLARE
  ld NUMERIC(18,2);
  lc NUMERIC(18,2);
BEGIN
  SELECT COALESCE(SUM(debit),0), COALESCE(SUM(credit),0)
    INTO ld, lc
    FROM journal_lines
   WHERE journal_id = NEW.id;

  IF ld <> lc THEN
    RAISE EXCEPTION 'Law 4: baris jurnal % tidak seimbang (D=% K=%)',
      NEW.journal_number, ld, lc;
  END IF;

  IF ABS(ld - NEW.total_debit) >= 0.01 OR ABS(lc - NEW.total_credit) >= 0.01 THEN
    RAISE EXCEPTION 'Law 4: baris jurnal % tidak sama dengan header (baris D=% K=%, header D=% K=%)',
      NEW.journal_number, ld, lc, NEW.total_debit, NEW.total_credit;
  END IF;

  RETURN NEW;
END; $$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_enforce_line_header_balance ON journal_entries;
CREATE TRIGGER trg_enforce_line_header_balance
  BEFORE UPDATE ON journal_entries
  FOR EACH ROW
  WHEN (NEW.status = 'POSTED' AND OLD.status IS DISTINCT FROM 'POSTED')
  EXECUTE FUNCTION enforce_line_header_balance();
```

### Titik pasang: `BEFORE UPDATE` saat transisi ke POSTED
Semua jalur memakai pola Law 20 DRAFT → sisipkan baris → UPDATE POSTED, jadi transisi itu satu-satunya
gerbang yang dilewati semuanya. `BEFORE` memastikan penolakan terjadi **sebelum**
`trg_assign_hash_sequence` menghitung hash/urutan — jurnal timpang tak pernah masuk rantai.

## Hal yang HARUS diperiksa sebelum diterapkan

1. **Jalur INSERT-langsung-POSTED.** Trigger ini hanya menangkap UPDATE. Perlu grep memastikan nol
   jalur yang `INSERT ... status='POSTED'` sekali jalan (Law 20 melarangnya, tapi harus dibuktikan,
   bukan diasumsikan). Kalau ada → tambahkan trigger kembar `BEFORE INSERT WHEN NEW.status='POSTED'`.
2. **Urutan vs trigger lain.** Ada 6 trigger di `journal_entries`; nama `trg_enforce_...` dipilih agar
   berjalan sebelum `trg_assign_hash_sequence` secara alfabetis (`e` < `a`? TIDAK — perlu dicek;
   PostgreSQL menjalankan trigger BEFORE per abjad nama. Kalau perlu, ganti nama jadi
   `trg_aa_enforce_line_header_balance`). **Verifikasi, jangan asumsikan.**
3. **Jurnal existing yang timpang.** `milkydb` punya 2 (`BP-2608-0001`, `VD-2608-0001`, net 0,
   permanen karena Law 2). Trigger hanya berlaku saat transisi, jadi baris lama tak terganggu —
   tapi pastikan nol proses yang meng-UPDATE ulang jurnal lama ke POSTED.
4. **Toleransi.** Pakai `0.01` konsisten dengan `je_balanced`, jangan `= 0` eksak (Law 25 numeric).
5. **Dampak kinerja.** Satu agregasi per posting; `journal_lines(journal_id)` sudah ter-index.

## Uji-bicara WAJIB sebelum dipercaya (Law 33)

Guard baru **tidak boleh** dipercaya hanya karena jalur normal masih hijau:

```
MERAH-1: DRAFT + baris timpang (D=100, K=90) → UPDATE POSTED → HARUS ditolak
MERAH-2: DRAFT + baris seimbang tapi header beda (baris 100/100, header 90/90)
         → UPDATE POSTED → HARUS ditolak
HIJAU-1: DRAFT + baris seimbang + header sama → UPDATE POSTED → HARUS lolos
HIJAU-2: golden path penuh (run_all.sh) → tetap 194 OK, nol regresi
```

Tanpa MERAH-1 **dan** MERAH-2 yang benar-benar dijalankan, trigger ini berstatus `[INFER]`.

## Konsekuensi yang diterima

Setelah trigger aktif, jalur mana pun yang selama ini diam-diam memposting timpang akan mulai
**gagal keras**. Itu tujuannya — tapi berarti perlu dijalankan lebih dulu terhadap golden path penuh
untuk menemukan jalur yang selama ini bocor tanpa ketahuan. **Jangan terapkan langsung ke produksi
tanpa run_all.sh hijau.**
