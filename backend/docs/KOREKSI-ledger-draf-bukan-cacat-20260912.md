# KOREKSI — "`/api/expenses/ledger` tak menampilkan draf" BUKAN cacat (12 Sep 2026)

**Klaim yang DICABUT.** Pesan commit `a1339054` menyebut ini sebagai
*"ketidaklengkapan unitku sendiri"*. **Keliru.** Pesan commit tak bisa diubah,
jadi koreksinya ditulis di sini supaya orang berikutnya tak mengejar cacat yang
tak pernah ada.

## Klaim aslinya

Sesudah beban bisa dibuat sebagai draf (`5cd020d9`), dilaporkan bahwa
`/api/expenses/ledger` tak mengembalikan draf sama sekali sementara
`/api/expenses` menampilkannya. Kuterima sebagai kelalaianku: membuat draf bisa
lahir tanpa memeriksa bahwa KEDUA daftar menampilkannya.

## Kenapa itu salah — terukur

`expenses.py` ~830, kueri inti `list_expense_ledger`:

```sql
WITH expense_ledger AS (
    ... FROM journal_entries je
    JOIN journal_lines jl ON jl.journal_id = je.id
    JOIN chart_of_accounts coa ON coa.id = jl.account_id
    WHERE je.tenant_id = $1
      AND je.status = 'POSTED'
```

Ia berangkat dari **jurnal**, bukan dari tabel `expenses`; tabel beban hanya
`LEFT JOIN` untuk metadata. **Draf tak punya jurnal, jadi ia tak bisa muncul —
bukan karena penyaring, melainkan karena BENTUK kuerinya.**

Tanda tangannya juga tak pernah menjanjikannya:
`status: Literal["all", "posted", "void"]` — **tanpa `"draft"`**.

Memaksa `ledger` memuat draf akan merusak jaminan yang membuatnya berguna:
*setiap baris di sini punya jurnal POSTED*. Docstring-nya menyebut Law 16.

**Kontrol** (kaos-biru-konveksi): seluruh 22 beban berjurnal ketemu jurnalnya —
pengecualiannya persis sebatas draf, tidak lebih. Dan `ledger` menangkap LEBIH
luas: `MANUAL` 65, `EXPENSE` 22, `BANK_TRANSACTION` 1 — jurnal berjenis beban
dari modul mana pun, yang `/api/expenses` takkan pernah tampilkan.

## Dua endpoint, dua pertanyaan, dua-duanya benar

| endpoint | menjawab |
|---|---|
| `/api/expenses` | **dokumen** beban — termasuk draf, termasuk yang tak berjurnal |
| `/api/expenses/ledger` | **jurnal** berjenis beban — lintas modul, selalu berjurnal |

Perbaikannya (kalau daftar desktop perlu menampilkan draf) ada di **FE**:
berpindah ke `/api/expenses`, atau menambah pil Draf yang membacanya dari sana.
**Nol perubahan BE.**

## Pelajaran

Aku menerima laporan cacat sebagai milikku **tanpa mengukur**, karena bentuknya
masuk akal dan aku memang baru menyentuh area itu. **Mengakui kesalahan tanpa
memverifikasinya adalah bentuk lain dari menyimpulkan-tanpa-ukur** — arahnya
saja yang merendahkan diri, ketepatannya tidak lebih baik. Dan biayanya nyata:
satu keputusan palsu sempat masuk antrean pemilik.

Catatan lanjutan: "0 baris" yang dilaporkan untuk `?status=draft` kemungkinan
besar **422 yang ditelan parser pemanggil** (nilai di luar `Literal`), bukan
daftar kosong — instans lain dari "tak bisa bertanya" yang jadi "jawabannya nol".
