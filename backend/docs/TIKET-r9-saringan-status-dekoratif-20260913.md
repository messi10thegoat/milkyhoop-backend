# TIKET — saringan status di R9 DEKORATIF (13 Sep 2026)

**Status:** TERBUKA. **Tidak diperbaiki** — membetulkannya langsung memerahkan dua
rekening atas data lama. **Putusan pemilik** (bentuknya sama dengan patok V241/V242).

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
