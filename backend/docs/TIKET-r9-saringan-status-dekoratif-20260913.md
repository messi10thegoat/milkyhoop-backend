# TIKET — saringan status di R9 DEKORATIF (13 Sep 2026)

**Status:** **check_3 DIBETULKAN + DIPATOK (V243, `fe8b1935`, 13 Sep 2026)** atas putusan
pemilik **pilihan A**. **Masih terbuka:** fungsi DB/kode lain berbentuk sama (lihat
bagian SAPUAN) dan **skill banksync Rule 9** (suntingan skill menunggu izin pemilik).

_Status lama: TERBUKA. Tidak diperbaiki — putusan pemilik._

**Lingkup verifikasi:** kueri baca-saja ke `milkydb` + pembacaan kode. Nol perubahan.

## Cacatnya

Invariant R9 (journal_lines vs bank_transactions) di **dua** tempat memakai bentuk:

```sql
LEFT JOIN journal_lines jl    ON jl.account_id = bc.coa_id
LEFT JOIN journal_entries je  ON je.id = jl.journal_id AND je.status = 'POSTED'
...
SUM(jl.debit) - SUM(jl.credit)
```

`je.status = 'POSTED'` di dalam `ON` sebuah **LEFT JOIN** hanya menolkan kolom `je`
untuk jurnal non-POSTED — **baris `jl`-nya tetap ada dan tetap dijumlahkan.** Saringan
status itu **tak pernah menyaring apa pun.** R9 menjumlahkan baris jurnal **berstatus
apa pun** (POSTED, VOID, DRAFT).

Tempatnya:
1. `monitoring/accounting_health_check.sh` — `check_3_bank_sync` (kueri cacah DAN kueri detail)
2. **skill `milkyhoop-banksync` Rule 9** — "THE INVARIANT" memuat bentuk yang sama
   (**Law 34**: dokumen konstitusi menjanjikan saringan yang tak bekerja)

## Bukti — gap POSTED-saja = PERSIS jurnal VOID lama

`kaos-biru-konveksi`, 13 Sep 2026:

| rekening | R9 resmi | R9 POSTED-saja | baris jurnal VOID di CoA itu |
|---|---|---|---|
| BCA Operasional | 0,00 | **51.848,00** | 6 baris, net **−51.848,00** |
| Bendahara | 0,00 | **13.370,00** | 10 baris, net **−13.370,00** |
| BCA Pengeluaran | 0,00 | 0,00 | — |
| Liturgi | 0,00 | 0,00 | — |

R9 resmi hijau **karena** jurnal VOID lama ikut dijumlahkan. Jurnal-jurnal VOID itu
adalah jurnal asli beban yang dulu dibalik ke `status='VOID'` sebelum perbaikan Law 2
(`84af5e59`) — data yang pemilik putuskan dibiarkan.

## Kenapa ini penting walau hari ini "benar secara kebetulan"

- **Penjaga yang tak bisa gagal pada dimensi status** (Law 33). Jurnal DRAFT yang
  menyentuh CoA bank tanpa pasangan btx akan ikut dijumlahkan dan bisa MENUTUPI selisih.
- **Kontrak gerbang yang bersandar padanya lebih lemah dari tulisannya.** Unit void
  transaksi bank (`a42ff853`) memasang "R9 gap 0,00" sebagai syarat; yang benar-benar
  membuktikan "void tak menggeser saldo" adalah assert terpisah atas versi POSTED-saja.

## Pilihan untuk pemilik (tak dipilih di sini)

- **A.** Betulkan kueri (pindahkan syarat status ke `WHERE`, atau pakai
  `is_effective_journal`) **+ patok identitas** untuk 16 jurnal VOID lama di dua
  rekening, meniru V241/V242 — alarm hanya berbunyi untuk kerusakan BARU.
- **B.** Betulkan kueri tanpa patok — dua rekening merah setiap pagi atas data lama.
- **C.** Biarkan, dicatat. Konsekuensi: R9 tetap buta terhadap status jurnal.

Apa pun pilihannya, **Rule 9 di skill banksync ikut dikoreksi** supaya kueri yang
disalin orang berikutnya tidak membawa saringan dekoratif yang sama.

---

# DIBETULKAN — V243 (`fe8b1935`, 13 Sep 2026), putusan pemilik A

- **Saldo buku = hanya jurnal POSTED**, syarat di `WHERE` (bukan `ON`).
- **Anggota per rekening aktif:** jurnal non-POSTED yang punya btx terikat ke rekening
  itu + baris **sisa** bila anggota tak menutup gap → setiap gap punya identitas.
- **Patok identitas** (`health_check_exemptions`, `check_name='bank_sync'`): 16 jurnal
  VOID lama, total **65.218** (BCA Operasional 6 / 51.848; Bendahara 10 / 13.370),
  diambil dari keadaan hidup. Jurnal non-POSTED baru, penggantian walau nominal sama,
  atau sisa baru → `FAIL_DRIFT_CHANGED`.
- `check_3` membaca `hc_verdict('bank_sync')`. Rollback: `V243__…_ROLLBACK.sql`
  **+ revert skrip** (tanpa revert, check_3 jadi BROKEN, bukan lulus).

## Bukti — `scripts/gerbang_v243_badan.sql`, 15/15, uji kering DAN atas fungsi hidup

| sisi | hasil |
|---|---|
| **kebutaan lama lewat EKSEKUSI**: suntik jurnal DRAFT + btx terikat | R9 **lama tetap 0** di kedua tenant; R9 **baru merah** |
| hijau berpatok / tenant sehat | PASS_EXEMPT / PASS |
| tanpa patok | FAIL_NON_EXEMPT |
| tambah: btx tanpa jurnal | FAIL_DRIFT_CHANGED (sisa) |
| ganti murni (drift+cacah+jumlah sama, identitas beda) | FAIL_DRIFT_CHANGED |
| tenant kosong | galat |
| V242 | tetap 3× PASS_EXEMPT |

Skrip harian ujung-ke-ujung: BROKEN 0; alarm tunggal tetap check_15 HIGH (tak terkait).

## SAPUAN — bentuk yang sama di tempat lain (TERUKUR, TIDAK disentuh)

Sapuan `pg_proc` (fungsi biasa) atas `LEFT JOIN journal_entries … ON … status = 'POSTED'`:

| tempat | saringan dekoratif | pemakai terukur | taruhan |
|---|---|---|---|
| `check_bank_sync_health()` | status | dipanggil sebagai guard R9 (skill banksync Rule 11) | penjaga — sama kelasnya dgn check_3 |
| `get_trial_balance()` | **tenant, status, tanggal as-of, periode** — semuanya di `ON` | **0 pemanggil SQL** ditemukan (nama `get_trial_balance` di kode bot = nama *tool* ke `/api/reports/trial-balance`, bukan fungsi ini) | kalau kelak dipakai: neraca saldo per tanggal menjumlahkan SEMUA jurnal |
| `compare_cost_centers()` | status + rentang tanggal | **dipanggil** `cost_centers.py:517` | laporan pusat biaya menjumlahkan jurnal non-POSTED & di luar rentang |
| `bank_accounts.py:1230` | join `je` ke `bt.journal_id` | jalur baca | **semantik beda** — dicatat, jangan disamakan hanya karena bentuk SQL mirip |

Masing-masing butuh pengukuran dampak sendiri sebelum diputuskan; **tidak** termasuk
putusan A (yang hanya untuk check_3).

---

# KOREKSI TANDA + CATATAN compare_cost_centers (13 Sep 2026)

## Tanda gap bank_sync

Patok dan drift tersimpan **+65.218** (BCA Operasional **+51.848**, Bendahara **+13.370**).
Rumus: gap = saldo buku (hanya jurnal POSTED) − Σ bank_transactions. **Positif = buku besar LEBIH
TINGGI dari catatan transaksi bank**: 16 jurnal beban asli berstatus VOID tak lagi dihitung di
buku, sementara mutasi bank keluar-nya (btx) tetap tercatat. Baris jurnal VOID itu sendiri
bernilai net −51.848 / −13.370 di CoA bank — itulah tanda minus yang sempat ditulis di atas.

## compare_cost_centers — dampak TERUKUR NOL, cacat laten di TIGA lapis

1. **Fungsi dekoratif** (status + rentang tanggal di `ON`) — kontrol positif sintetis di ROLLBACK:
   fungsi lama 301.234 (memasukkan jurnal DRAFT + jurnal di luar rentang) · saringan diterapkan 0 ·
   rentang diperlebar 300.000 (hanya yang sah). Stimulus pertama (menandai baris POSTED nyata)
   ditolak trigger nyata `prevent_posted_journal_line_modification`.
2. **Tanpa data**: `cost_centers` 0 baris di semua tenant; baris jurnal ber-`cost_center_id` 0.
3. **Rute tertutup**: `GET /api/cost-centers/{cost_center_id}` (#656) terdaftar sebelum
   `/comparison` (#662) → pencocokan pertama untuk `/comparison` = rute detail (TERUKUR lewat
   pencocok starlette + kontrol `/tree` dan `/<uuid>`). Status kode HTTP-nya DUGAAN.

Pemakai: FE 0 berkas (kontrol grep `bank-transfers` 8 berkas); bot memakai `/api/cost-centers`
dan `/{id}/summary` — `get_cost_center_summary` BENAR (INNER JOIN + WHERE), bukan anggota kelas ini.

⚠️ **Urutan perbaikan yang aman kalau suatu hari dibuka: betulkan fungsi DULU, baru buka rute.
Sebaliknya membuka angka salah seketika** — dua cacat saling menutupi; memperbaiki yang satu
menyalakan yang lain.
