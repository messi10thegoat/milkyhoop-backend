# P0: `idempotency_keys` PK global — bentrok LINTAS TENANT, dorman karena implementasi buruk

**Tanggal:** 2026-08-06 **Severity:** P0 (isolasi tenant + kegagalan transaksi)
**Status:** migration **V221** ditulis + diuji dua arah di clone; apply ke live = langkah terpisah
**Kelas bukti:** `[SQL]` runtime, bukan inferensi

## Fakta

```
Indexes:
  "idempotency_keys_pkey"      PRIMARY KEY, btree (key)          <- key SAJA
  "idx_idempotency_tenant_key" UNIQUE,      btree (tenant_id, key)
```

Kode memakai `ON CONFLICT (tenant_id, key) DO NOTHING` (mis. `sales_invoices.py:2866`,
`utils/idempotency.py:101`). **`ON CONFLICT (tenant_id, key)` tidak menangkap pelanggaran PK `(key)`.**

Bukti runtime (`milkydb_fresh`, 2026-08-06):
```
INSERT key='RCV:same-key' tenant='tenant-A'                      -> INSERT 0 1
INSERT key='RCV:same-key' tenant='tenant-B' ON CONFLICT (tenant_id,key) DO NOTHING
  -> ERROR: duplicate key value violates unique constraint "idempotency_keys_pkey"
  -> current transaction is aborted, commands ignored until end of transaction block
```

Akibatnya **bukan** "duplikat tertolak" melainkan **seluruh transaksi tenant B GUGUR** — pembayarannya
gagal total, hanya karena tenant lain kebetulan memakai string kunci yang sama.

## Bentuk bug ini penting: DORMAN karena implementasi sekarang BURUK

Selama kunci idempotency **acak (UUID)**, tabrakan lintas tenant praktis mustahil → bug tak terlihat.
Rencana pindah ke **kunci deterministik sisi-server** (Law 14 lapis 1) justru **MENGAKTIFKANNYA**:
kunci menjadi string yang diturunkan dari data domain, sehingga dua tenant dengan pola sama
menghasilkan string sama.

**Perbaikan yang direncanakan akan meledakkan ranjau yang dipasang oleh implementasi yang hendak
diperbaiki.** Karena itu V221 WAJIB mendahului lapis 1.

Saat ini tak terlihat karena tenant hanya **satu**.

## Model yang keliru, bukan sekadar risiko

`idempotency_keys` adalah tabel multi-tenant (`tenant_id NOT NULL`; semua query memfilter
`WHERE tenant_id = $1 AND key = $2`). PK `(key)` **menyatakan** "kunci ini unik secara GLOBAL lintas
tenant" — pernyataan yang salah tentang domainnya sejak awal. Kunci idempotency adalah properti
**(tenant, operasi)**.

V221 = **koreksi model**, bukan tambalan.

## Cek KELAS (window termurah, dilakukan sekaligus)

Seluruh tabel multi-tenant yang PK-nya tak memuat `tenant_id` — hanya **7**:

| Tabel | PK | Penilaian |
|---|---|---|
| **`idempotency_keys`** | `key` | 🔴 natural key deterministik — satu-satunya yang berbahaya |
| `chat_session_state` | `session_id` **uuid** | 🟢 acak |
| `userguide_query_log` | `log_id` **uuid** | 🟢 acak |
| `document_intake_log` + 3 partisi | `id, ts` | 🟢 surrogate |

Sisanya `PRIMARY KEY (id)` surrogate. **Nol kandidat kedua** — kelasnya nyata tapi instance aktifnya
tunggal, justru karena hanya tabel ini yang kuncinya bukan acak.

## V221 — uji dua arah, 4/4 lulus (clone, bukan live)

| Uji | Hasil |
|---|---|
| MERAH baseline: tenant A+B key sama | `ERROR duplicate key ... idempotency_keys_pkey` ✅ |
| HIJAU-1 sesudah: tenant A+B key sama | 2 baris, nol error ✅ |
| HIJAU-2: tenant sama, key sama | `DO NOTHING` bekerja — 1 baris, `result` asli TIDAK tertimpa ✅ |
| Idempoten: V221 diulang | `NOTICE: PK sudah komposit — dilewati` ✅ |

HIJAU-2 sengaja memeriksa **isi** `result`, bukan hanya jumlah baris: kalau `DO NOTHING` diam-diam
menjadi `DO UPDATE`, hasil replay tertimpa dan bug itu tak terlihat dari hitungan baris.

`idx_idempotency_tenant_key` di-DROP karena setelah PK komposit ia punya kolom + urutan **identik**
= dua index kembar, biaya tulis ganda, nol manfaat.

## Rollback
`V221_reverse.sql` ditulis SEBELUM apply. Plus snapshot
`/root/milkydb_walkthrough_20260806.sql.gz`
(sha256 `e16a2429…`, **uji restore terverifikasi**: 13 jurnal / 1 tenant / 1 faktur / 1 DP — identik live).

---

## METODE (bukan bug — layak jadi kebiasaan)

**Memeriksa satu invariant sering membongkar invariant tetangga.**

Temuan ini muncul saat memeriksa **atomicity** (apakah baris idempotency ditulis dalam transaksi yang
sama dengan operasinya). Atomicity-nya ternyata **AMAN** — tapi perjalanan memeriksanya memaksa
membaca skema tabel itu baris demi baris, dan di situlah PK global terlihat.

Kalau pertanyaan atomicity tak pernah diajukan, PK global akan tetap dorman sampai tenant kedua
memakai kunci deterministik — yaitu tepat saat kerugiannya paling mahal dan paling sulit didiagnosa.

**Aturan turunan:** saat memverifikasi satu invariant, **baca seluruh definisi objek yang
disentuhnya** (skema tabel, index, constraint, trigger), jangan hanya bagian yang menjawab pertanyaan.
Invariant tetangga sering rusak dengan cara yang tak pernah memicu gejala sendiri.

Instance lain dari kebiasaan yang sama di sesi ini:
- memperbaiki kolom hantu `content_unit` → audit SEMUA 13 kolom endpoint → menemukan sisanya sah
- memperbaiki biaya bank → membaca `je_balanced` → menemukan CHECK header-only (94 jalur)
- memeriksa cakupan guard → menghitung jalur posting → menemukan nol kernel terpusat
