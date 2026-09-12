# TEMUAN — sapuan izin rute (12 Sep 2026)

**LAPORAN SAJA. Nol perubahan kode, nol perubahan izin.** Pemilik yang memutuskan
mana yang ditutup.

**Lingkup verifikasi:** enumerasi dari **tabel rute aplikasi yang BERJALAN**
(`app.routes`, 1.077 rute unik), bukan dari nama berkas — nama berkas adalah
kelas yang menghasilkan "12 ternyata 14". Pencocokan dilakukan ke **dua arah**.
Tak satu pun endpoint destruktif dipanggil untuk mengujinya.

---

## 0. UKURAN PAPARAN — baca ini sebelum menilai kegentingannya

| tenant | peran | jml | aktif |
|---|---|---|---|
| adhita-ariyani | Owner | 1 | 1 |
| grapgrap-manado | Owner | 1 | 1 |
| kaos-biru-konveksi | **Collaborator** | 1 | 1 |
| kaos-biru-konveksi | Owner | 1 | 1 |
| subbidel-kolsani | Owner | 1 | 1 |

**Pemilik melewati SELURUH lapis izin** lewat bypass OWNER di `can()`
(`policy_engine_client.py:294`). Jadi yang terpapar hanya anggota **non-owner**.

**Di seluruh basis data hari ini hanya ADA SATU anggota non-owner: akun uji
`uji.nonowner+mh@resend.dev` yang dibuat untuk gerbang.** Artinya paparan nyata
saat ini **mendekati nol**.

**Tapi**: begitu satu pembukuh/staf diundang — tujuan modul Tim & Akses — setiap
rute di bawah ini terbuka baginya. Jadi ini **utang yang wajib lunas SEBELUM
anggota pertama masuk**, bukan kebakaran hari ini.

---

## 1. ARAH 1 — rute TULIS tanpa pola penjaga

```
rute unik               1.077
rute TULIS                509
  terjaga pola            160
  di SKIP_PATTERNS         20
  TANPA POLA              329   <-- ini
```

**Tak ada lapis kedua yang menyelamatkan.** Diukur:
- `require_permission()` ada di `dependencies/permissions.py:132` tapi
  **NOL kemunculan** di seluruh `routers/`.
- `APIRouter(dependencies=[...])` hanya 1× — `dashboard.py:22`, dan itu
  `require_active_membership` (keanggotaan, **bukan** izin).
- `include_router(..., dependencies=)` **nol**.
- Middleware terpasang polos: `main.py:324 app.add_middleware(PermissionMiddleware)`,
  tanpa `skip_permission_check`.

### Yang DIVERIFIKASI PERILAKUNYA (14 probe, 14 telanjang)

Akun uji non-owner, **UUID yang tak ada** sehingga handler berhenti sebelum
berbuat apa pun. `403` = terjaga · `404` = handler jalan · **`422` = body sempat
DIURAIKAN, bukti terkuat bahwa lapis izin dilewati sepenuhnya**.

| rute | hasil |
|---|---|
| `POST /api/bank-transfers/{id}/void` | **422** |
| `POST /api/bank-transfers/{id}/post` | 404 |
| `POST /api/payroll-payments/{id}/post` | 404 |
| `POST /api/payroll-payments/{id}/void` | **422** |
| `POST /api/production/month-end-reconcile/{id}/void` | 404 |
| `POST /api/sales-receipts/{id}/void` | **422** |
| `POST /api/budgets/{id}/close` | 404 |
| `POST /api/purchase-orders/{id}/close` | 404 |
| `DELETE /api/documents/{id}` | 404 |
| `DELETE /api/stock-adjustments/{id}` | 404 |
| `DELETE /api/purchase-orders/{id}` | 404 |
| `DELETE /api/fixed-assets/{id}` | 404 |
| `PATCH /api/items/{id}/status` | 404 |
| `POST /api/items/{id}/stock-adjustment` | **422** |

⚠️ **329 adalah ENUMERASI; 14 adalah yang DIVERIFIKASI.** Sisanya belum diuji
satu per satu — jangan dibaca sebagai 329 terbukti.

### [A] Taruhan tertinggi (`file:baris`)

**KOREKSI 12 Sep:** [A] bukan 30 penulis jurnal. Diklasifikasi ulang dari
**sumber handler utuh** (via `inspect`, bukan jendela baris tetap):
**25 penulis + 5 `preview-journal` yang BACA-SAJA** (nol
`INSERT`/`UPDATE`/`DELETE`; dua di antaranya mendokumentasikan
"READ-ONLY, nol tulis"). Kelimanya masuk [A] karena kata "journal" ada di
jalurnya — **inferensi dari nama**, persis cacat yang laporan ini bahas.
Mereka diberi aksi `R`, bukan `P`.

Juga terkoreksi: lima handler **mendelegasikan** ke service
(`bills/{id}/payments`, `payment-requests/cancel`+`mark-paid`,
`bank-transfers/{id}/post`, `sales-invoices/{id}/fulfill`) — cacah SQL di
handler buta terhadapnya, jadi "nol tulis" di situ **bukan** baca-saja.

```
DELETE /api/journals/{journal_id}                          routers/journals.py:763
POST   /api/bank-transfers/{id}/post                       routers/bank_transfers.py:937
POST   /api/bank-transfers/{id}/void                       routers/bank_transfers.py:979
POST   /api/payroll-payments/{id}/post                     routers/payroll_payments.py:102
POST   /api/payroll-payments/{id}/void                     routers/payroll_payments.py:248
POST   /api/production/month-end-reconcile/{id}/void       routers/production.py:3587
POST   /api/sales-receipts/{id}/void                       routers/sales_receipts.py:748
POST   /api/sales-invoices/{id}/fulfill                    routers/sales_invoices.py:5386
POST   /api/bills/{id}/payments                            routers/bills.py:1076
POST   /api/fiscal-years/{id}/close                        routers/fiscal_years.py:235
POST   /api/fixed-assets/post-depreciation                 routers/fixed_assets.py:793
POST   /api/customers/{id}/opening-balance/reverse         routers/customers.py:1480
POST   /api/budgets/{id}/close                             routers/budgets.py:372
POST   /api/purchase-orders/{id}/close                     routers/purchase_orders.py:1541
POST   /api/intercompany/reconcile                         routers/intercompany.py:754
```
Ditambah 15 lagi di kategori sama (approve, settle, mark-paid, preview-journal,
agentic-reconcile, tables/sessions close) — total **30**.

**`month-end-reconcile/{id}/void` patut disorot**: FRONTEND sedang memasang
dialog konfirmasi di situ. Dialognya akan menjaga dari salah-klik, **bukan** dari
pengguna tanpa izin.

**Tak dipanggil, sengaja:** `fixed-assets/post-depreciation` dan
`intercompany/reconcile` tak punya parameter id — memanggilnya bisa benar-benar
bertindak. **Belum dipastikan**; butuh pembacaan kode atau subjek uji khusus.

### [B] Menulis data — 299 rute
Lampiran penuh ada di keluaran `scripts/sapu_izin.py`. Contoh terverifikasi
telanjang: `DELETE /api/documents/{id}`, `DELETE /api/fixed-assets/{id}`,
`PATCH /api/items/{id}/status`, `POST /api/items/{id}/stock-adjustment`.

### Di luar `/api`
3 rute tulis (`POST /chat/chat/`, `/chat/tenant/{id}/chat`, `/{tenant_id}/chat`)
dan 3 rute `POST /api/test/...` hidup di produksi.

---

## 2. ARAH 2 — pola yang TAK PERNAH cocok (31)

Pola yang ada tapi tak menjaga apa pun. **Lebih berbahaya daripada pola yang
hilang**: ia menenangkan pembaca tabel.

### 2a. Mati tak berbahaya — rutenya memang tak ada
`production/work-orders`, `work-centers`, `/api/ar`, `/api/aging/ar`,
`/api/aging/ap`, `customers/summary`, `vendors/summary`, `payroll/summary`,
`bill-payments/{id}` PATCH/PUT, `reports/.*/export`, `payroll/{id}` DELETE,
`production/material-issues` POST, `production/fg-receipts` POST.
→ sampah tabel; menggelembungkan **kesan** cakupan.

**KOREKSI 12 Sep (sesudah commit pertama):** dua yang terakhir semula
kutempatkan di 2b ("menipu"). Salah — diukur ulang, **tak ada rute POST**
untuk keduanya, hanya `GET`. Akarnya: aku memakai satu grep bercabang dua
dan tak pernah memverifikasi tiap cabangnya terpisah. Kelompok 2b menyusut
dari 6 jadi **4**.

### 2b. Mati karena SALAH BENTUK padahal rutenya ADA — ini yang menipu

| pola (tak pernah cocok) | rute nyata yang ia kira dijaga |
|---|---|
| `^/api/bpjs` | `GET`+**`PUT`** `/api/payroll-config/bpjs` |
| `^/api/team-members$` POST | **`POST /api/team-members/invite`** |
| `^/api/approval-inbox`, `^/api/approvals` | keluarga `/api/approval-*` (19 rute) |
| `^/api/payment-requests/[^/]+$` PATCH/DELETE | 7 rute di prefiks itu |

⚠️ **`POST /api/team-members/invite` adalah yang paling berkonsekuensi**: ia
mekanisme menambah anggota. **BELUM DIPASTIKAN** — probe yang berhasil akan
benar-benar **mengirim undangan**, jadi tidak dipanggil. Untuk memastikannya
dibutuhkan subjek uji khusus atau pembacaan kode.

---

## 3. Yang TIDAK dilakukan, dan sebabnya

- **Nol endpoint destruktif dipanggil.** Semua probe memakai UUID tak ada.
- **`team-members/invite` tidak diprobe** — akan mengirim undangan sungguhan.
- **Bypass OWNER dinyatakan dari KODE, bukan runtime** — mengujinya menuntut
  login sebagai pemilik, dan itu memicu `force_logout` yang mencabut layarnya.
  **Belum terbukti berjalan; bukan hijau.**
- **Nol perubahan** pada kode, pola, atau baris izin.

---

## 4. Kelas kesalahannya (kenapa ini terjadi tanpa ada yang salah menulis kode)

Penjagaan lewat **enumerasi pola** melewatkan anggota **tanpa gagal**. Menambah
endpoint tanpa menambah polanya membuka lubang **tanpa satu baris kode yang
salah** — tak ada tes yang merah, tak ada galat, tak ada peringatan.

Pasangannya: **terjaganya satu endpoint tak mengatakan apa pun tentang
tetangganya.** Di beban, `PATCH`/`DELETE` menolak 403 dengan benar sementara
`/void` persis di sebelahnya meloloskan semua orang.

**Ditemukan oleh instrumen yang GAGAL**: ramalan 403-ku meleset jadi 404 saat
memverifikasi deploy lain. Kalau ramalanku tepat, celah ini tak ketahuan hari
itu. Selisih antara ramalan dan hasil adalah tempat temuan berada.

---

## 5. Usulan urutan (pemilik yang memutuskan)

1. **Sebelum anggota non-owner pertama diundang** — tutup [A] (30 rute penulis
   jurnal). Polanya meniru yang sudah terbukti bekerja untuk 5 modul + beban.
2. Perbaiki **2b** (6 pola salah bentuk) — prioritaskan `team-members/invite`,
   karena ia pintu masuk semua anggota berikutnya.
3. Buang **2a** (11 pola sampah) supaya tabelnya tak lagi melebih-lebihkan
   cakupannya.
4. [B] (299 rute) — sapuan tersendiri, bukan satu unit.
5. **Penjaga struktural**: uji yang gagal kalau ada rute tulis tanpa pola. Tanpa
   ini, celahnya tumbuh lagi diam-diam pada endpoint berikutnya.

---

## 6. TINDAK LANJUT — ditutup 12 Sep 2026 (unit terpisah)

Atas putusan pemilik: [A] + 3 pola salah bentuk + penjaga struktural.
**299 rute [B] TIDAK dikerjakan** (sapuan tersendiri), dan **11 pola sampah 2a
TIDAK dicabut** (lebih aman dibiarkan daripada dicabut tergesa).

| | sebelum | sesudah |
|---|---|---|
| pola di `ROUTE_PERMISSIONS` | 211 | **242** |
| rute tulis terjaga | 160 | **193** |
| rute tulis TANPA pola | 329 | **296** |

**30/30 pola baru terbukti mencocokkan rute yang dituju** — diuji lewat
pencocok, bukan panggilan hidup, karena menambah pola yang tak pernah menyala
adalah persis cacat yang sedang diperbaiki.

**Penjaga struktural** `tests/unit/test_pagar_rute_izin.py` + inventaris +
garis dasar (296 baris, wajib MENYUSUT), dengan pasangan gerbang kesegaran
`scripts/gate_izin_rute.py`.

**Rancangan yang DITOLAK**: parse statis berkas router — diukur buta terhadap
**14 rute hidup** (termasuk seluruh keluarga `/api/payroll/{run_id}/post|void`)
dan mengarang **45** yang tak ada. Penjaga buta gagal DIAM-DIAM; inventaris basi
gagal BERISIK. Itu sebabnya inventaris dihasilkan dari `app.routes`.

**Dibuktikan BISA MERAH** (3 sabotase, masing-masing dipulihkan):
rute baru tanpa pola → assertion utama merah · entri garis dasar basi →
assertion anti-beku merah · inventaris dikosongkan → kontrol positif merah.
⚠️ Sabotase ketiga **tidak terisolasi**: inventaris kosong juga memerahkan
assertion anti-beku (akibat wajar). ⚠️ `test_pola_terbaca_dalam_jumlah_wajar`
**belum** dibuktikan merah.
