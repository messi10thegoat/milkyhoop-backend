# TIKET — void rekonsiliasi akhir bulan tak bisa beralasan (12 Sep 2026)

**Status:** TERBUKA. Bukan unit hari ini; ditulis supaya temuannya tidak hanya
hidup di pesan lintas-sesi.

**Lingkup verifikasi:** yang diperiksa **kode + skema**, BUKAN eksekusi. Tak ada
jurnal sungguhan yang di-void untuk mengujinya.

## Cacatnya

`POST /api/production/month-end-reconcile/{journal_id}/void` **tidak menerima
body sama sekali** — tak ada `reason` maupun nama lain.

```python
# backend/api_gateway/app/routers/production.py:3588-3590
@router.post("/month-end-reconcile/{journal_id}/void", ...)
async def void_month_end_reconcile(request: Request, journal_id: UUID):
```

Nol parameter body: apa pun yang dikirim klien tak pernah di-parse.

## Yang membuatnya HALUS: tempatnya sudah ada, sumbernya yang dipatok

Ini bukan "jejak auditnya kosong". Alasannya **tercatat** — tapi konstan:

```python
# production.py:3658
rev_id = await _reverse_journal(conn, tenant_id, user_id, journal_id,
                                "Void month-end manufacturing reconcile")
```

Helper `_reverse_journal(..., reason: str)` (`production.py:1228`) menulis
`reason` ke DUA tempat pada jurnal pembalik:

- `description` = `f"REVERSAL {journal_number}: {reason}"`
- kolom `reversal_reason`

Kolom penampungnya nyata (terukur di `information_schema`, DB `milkydb`):
`journal_entries.reversal_reason :: text`, `void_reason :: text`,
`description :: text`.

Jadi keadaannya **seragam, bukan kosong**: setiap void reconcile menuliskan
kalimat yang sama persis. Ia tidak berbohong dan tidak mengarang sebab — ia
hanya tak memberi tahu apa-apa. Bedanya penting: ini BUKAN kelas `reason: `
atau `reason: "Batal"` yang dicabut dari dua situs Penjualan.

## Kenapa murah diperbaiki: polanya sudah ada di rumah

Endpoint reverse generik SUDAH menerima alasan pengguna dan menyimpannya lewat
jalur yang sama:

```python
# backend/api_gateway/app/schemas/journals.py:89
class ReverseJournalRequest(BaseModel):
    reversal_date: date
    reason: str = Field(..., min_length=1, max_length=500)
```

`journals.py:620` menyimpan `body.reason` ke `reversal_reason` + `description`.

Perubahan yang dibutuhkan = tambah model body ke endpoint void + ganti konstanta
dengan `body.reason`. **Helper, kolom, dan penulisannya tak perlu disentuh.**

## Angka yang mengubah taruhannya

```sql
SELECT count(*) FROM journal_entries
WHERE reversal_of_id IN (
  SELECT id FROM journal_entries WHERE source_type = 'PRODUCTION_RECONCILE'
);
-- 0
```

**Jalur void ini belum pernah dijalankan sekali pun di produksi.** Akibatnya dua
arah:

- **Nol backfill.** Tak ada baris lama beralasan-konstanta yang perlu ditambal.
- **Belum teruji runtime.** Jangan berasumsi jalurnya mulus hanya karena kodenya
  terbaca benar. Pemakai pertamanya akan jadi penguji pertamanya.

## Akibat bagi FE (kenapa tiket ini muncul)

FE sedang memasang konfirmasi aksi destruktif. Void reconcile adalah yang paling
mahal — ia MEMBALIK jurnal rekonsiliasi. Syaratnya: void yang menulis jurnal
pembalik wajib beralasan DARI PENGGUNA.

Selama tiket ini terbuka, FE **menahan kotak alasannya** dan memasang dialog
tanpa kotak itu. Itu sikap yang benar: dialog yang meminta alasan lalu
membuangnya adalah ilusi jejak audit — pengguna percaya jejaknya ada, dan yang
membaca buku setahun lagi tak punya apa pun.

Kalau dialognya perlu menyebut sesuatu: sebab void ini **tercatat otomatis**
(itu benar). Jangan janjikan sebab dari pengguna sampai BE berubah.

## Catatan enumerasi

`openapi.yaml` di repo FE **tidak memuat endpoint ini sama sekali**. Spec itu
ditulis tangan dan basi; ia pernah membuat `PaymentCreate` bertipe hijau lalu
ditolak server 422. Jawaban di atas diambil dari KODE, bukan spec.

---

# TAMBAHAN 12 Sep 2026 — koreksi angka + biaya subjek uji

Ditulis sesudah memeriksa klaim batasan FE ("tak ada cara murah membuat subjek
rekonsiliasi buatan tes"). Hasilnya mengoreksi angka di atas dan mengubah
sebab kenapa batasan itu tetap berlaku.

## KOREKSI: angkanya lebih besar dari yang kutulis

Di atas kutulis "0 baris PEMBALIK ber-`source_type=PRODUCTION_RECONCILE`".
Itu benar tapi terlalu sempit. Diukur ulang **tanpa penyaring tenant sama
sekali**:

```sql
SELECT tenant_id, count(*) FROM journal_entries
WHERE source_type = 'PRODUCTION_RECONCILE' GROUP BY 1;
-- 0 rows
```

**Jurnal rekonsiliasinya SENDIRI nol, di SELURUH tenant.** Jadi bukan hanya
jalur `/void` yang belum pernah jalan — **seluruh fitur rekonsiliasi akhir bulan
belum pernah dijalankan di produksi.** Void hanyalah hilir dari sesuatu yang
hulunya pun belum pernah dipakai.

## Subjek uji SEBENARNYA tersedia — tapi bukan milikku untuk dijalankan

Klaim "tak ada subjek murah" **tidak benar secara teknis**. Terukur:

- `kaos-biru-konveksi` punya **31 `PRODUCTION_LABOR` + 30 `PRODUCTION_OVERHEAD`**
  ber-status POSTED, semuanya 2026-09-03.
- Periode `2026-09` **OPEN** di keempat tenant.
- Ketiga peran wajib (`WIP_GENERIC`, `COGS_VARIANCE_PRODUCTION`,
  `INVENTORY_MERCHANDISE`) **sudah terpetakan di keempat tenant**.
- `ACTUAL_OVERHEAD` **tidak terpetakan di tenant mana pun** → kaki OH dilewati
  dengan anggun (`production.py` ~3186: `AccountRoleUnmappedError` → skip),
  tapi kaki labor aktif.

Karena `labor_active` benar, cabang `if not labor_active and not oh_active`
(`production.py` ~3318) TIDAK diambil, dan sebuah POST reconcile untuk periode
`2026-09` **akan benar-benar menerbitkan jurnal**. Subjeknya ada, sekarang.

**Tapi `kaos-biru-konveksi` adalah tenant HIDUP milik pemilik** — kredensial uji
kita = identitas pemilik. Menjalankan reconcile di sana bukan "uji murah": ia
menulis jurnal akuntansi sungguhan ke pembukuan pemilik, lalu void-nya menulis
jurnal pembalik sungguhan. Itu putusan pemilik, bukan biaya teknis.

**Kesimpulan: keputusan FE MENAHAN assertion eksekusi tetap BENAR — tapi
sebabnya bukan "subjeknya mahal dibuat". Sebabnya: subjeknya ada, dan justru
karena itu menjalankannya berarti menulis ke buku pemilik.**

## Satu-satunya penyelidikan yang benar-benar gratis

`dry_run` adalah **baca murni** — tanpa advisory lock, tanpa INSERT, tanpa 409:

```
POST /api/production/month-end-reconcile   {"period": "2026-09", "dry_run": true}
```

Komentarnya menyebut angkanya datang dari komputasi yang IDENTIK dengan jalur
tulis (anti-drift). Jadi prasyarat, saldo clearing, dan varians bisa
diverifikasi tanpa menulis apa pun. `dry_run` TIDAK menyentuh `/void`, jadi ia
tak bisa membuktikan jalur void — tapi ia membuktikan bahwa subjeknya nyata.

## Lingkup verifikasi tambahan ini

Kode + skema + **kueri baca** ke DB produksi. Tak ada reconcile yang dijalankan,
tak ada `dry_run` yang dipanggil, tak ada jurnal yang ditulis atau di-void.

---

# KOPLING FE — jangan tinggalkan janji lama di layar

Dilaporkan sesi FRONTEND, 12 Sep 2026.

Dialog konfirmasi void reconcile kini berbunyi:

> "Sebabnya tercatat otomatis pada jurnal pembaliknya"

Kalimat itu **benar hari ini** dan sengaja dipilih: ia menjanjikan persis yang
sistem lakukan (konstanta ke `reversal_reason` + `description`), tanpa mengaku
ada orang yang memutuskan.

**Begitu BE menerima `body.reason`, kalimat itu BERHENTI benar** — sebabnya tak
lagi otomatis, melainkan dari pengguna.

Jadi perubahan BE di tiket ini **tidak berdiri sendiri**. Yang menutup tiket:

1. BE: model body + `body.reason` menggantikan konstanta di `production.py:3658`.
2. FE: hidupkan kotak alasan (`{}` → `{ reason }`) **DAN** ganti kalimat dialog
   itu, supaya layar tak menjanjikan "otomatis" untuk sebab yang kini diketik
   pengguna.

Mengerjakan (1) tanpa (2) meninggalkan janji lama di layar — kelas yang sama
dengan label yang lebih akurat daripada perilakunya, hanya terbalik arah.
Kirim satu pesan ke sesi FRONTEND saat (1) mendarat; FE-nya kecil.
