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
