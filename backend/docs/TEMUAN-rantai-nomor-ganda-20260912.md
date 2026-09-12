# TEMUAN — nomor rantai ganda: akarnya trigger TERTUNDA (12 Sep 2026)

**Status:** TERBUKA. **Nol perubahan** — data tidak disentuh, kode tidak
disentuh. Perbaikannya putusan pemilik.

**Ringkas:** 9 pasang `chain_sequence` ganda + 2 hash pecah di
`kaos-biru-konveksi`. **Uangnya benar** (16/16 punya pembalik, dampak bersih
Rp 0,00); yang cedera **jejak audit**.

---

## AKARNYA — dan ia bukan urutan pernyataan

```sql
CREATE CONSTRAINT TRIGGER trg_assign_hash_sequence
AFTER INSERT OR UPDATE ON public.journal_entries
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION assign_hash_and_sequence()
```

**`DEFERRABLE INITIALLY DEFERRED` = trigger menyala di COMMIT**, bukan saat
pernyataannya jalan. Di jalur void:

```
1. pembalik INSERT 'DRAFT'         -> trigger diantre
2. pembalik UPDATE -> 'POSTED'     -> trigger diantre
3. asli     UPDATE -> 'VOID'       -> trigger diantre
4. COMMIT -> trigger jalan. Aslinya SUDAH VOID.
   MAX(chain_sequence WHERE status='POSTED') tak menghitungnya
   -> pembalik mewarisi NOMOR ASLINYA.
```

⚠️ **Menukar urutan pernyataan TIDAK memperbaiki apa pun.** Trigger tertunda
hanya melihat keadaan AKHIR transaksi. Dua sesi menghabiskan waktu berdebat
"balik-dulu vs posting-dulu"; **keduanya tak relevan**.

## Kondisi tabrakan — terukur, nol pengecualian

| | jml | `jurnal_di_antara` | `max` saat pembalik lahir |
|---|---|---|---|
| ganda | 9 | **0** | **= seq asli** |
| tidak | 7 | ≥ 2 | > seq asli |

Tabrakan terjadi **tepat ketika jurnal yang di-void sedang memegang nomor
tertinggi**. Batch-void (beberapa dibuat, lalu di-void berurutan) selamat;
**buat-lalu-void-langsung selalu bertabrakan** — dan itu pola yang dipakai
gerbang otomatis.

## Gejala kedua: 2 hash pecah — akar yang SAMA

`verify_chain_integrity` menelusuri `WHERE status='POSTED'`. Pembalik yang
`previous_hash`-nya menunjuk jurnal yang kemudian jadi VOID tak bisa
direkonstruksi: penelusur melompati yang VOID.
Terkena: `REV-EXP-2609-0001` (seq 109) dan `REV-EXP-2609-0015` (seq 393).

## Lingkup: LIMA jalur, bukan satu

```
expenses.py:2066 · bank_transfers.py:1110 · credit_notes.py:2010
credit_notes.py:2088 · vendor_credits.py:1836
```
Semua `SET reversed_by_id = $2, status = 'VOID'`, urutan identik.
Hanya beban yang **sudah** menghasilkan kerusakan — karena hanya beban yang
sudah di-void 16x di tenant ini. **Empat lainnya belum meledak, bukan benar.**

⚠️ Bukan bagian dari lima: `bills_service.py:2198` (→ `accounts_payable`) dan
`sales_invoices.py:4197` (→ `accounts_receivable`) — tabel lain, bukan Law 2.

## Penjaganya BEKERJA — tak ada yang menjalankannya

`accounting_health_check.sh` Check 2 meng-assert dengan benar. Dijalankan
12 Sep: `CRITICAL [2] Hash Chain: 2 broken chain links`, `EXIT 2`.
**`crontab -l` kosong, nol berkas log, nol systemd timer.**
Bukan penjaga buta, bukan merah yang diabaikan — **penjaga yang bekerja yang tak
terjadwal.** Perbaikan termurah dari seluruh temuan ini: satu baris jadwal.

## Law 22 tanpa pagar DB

Hanya `idx_je_chain_seq` btree `(tenant_id, chain_sequence)` — **bukan UNIQUE**.
Dokumen Law 2 menulis *"the unique index is NOT status-filtered"*, mengandaikan
indeks unik yang **tak pernah ada**. Iron Law 34 menimpa dokumen Law 34 sendiri:
klaim yang terdengar spesifik justru menghentikan pemeriksaan.

## Tiga arah (pemilik yang memilih)

1. **Berhenti membalik ke VOID** — instruksi Law 2 apa adanya. Aslinya tetap
   POSTED saat COMMIT, MAX tetap menghitungnya, tabrakan berhenti.
   Menyentuh lima jalur; **tak menyembuhkan 9+2 yang sudah ada**.
2. **Jadikan trigger non-deferred** — bekerja, tapi mengubah perilaku SETIAP
   jalur posting di sepuluh modul. **Tidak disarankan sebagai langkah pertama.**
3. **Pasang UNIQUE index** pada `(tenant_id, chain_sequence)` — menghentikan
   tabrakan baru secara keras, tapi **akan GAGAL selama 9 pasang lama masih
   ada**, jadi ia menuntut putusan soal data lama lebih dulu.

**Memperbaiki 9 pasang lama = menyunting jurnal POSTED** (Law 2/3) — putusan
pemilik, bukan kerapian.

---

## BENTUK LAW 34 YANG BERBEDA: hukumnya BENAR, alasannya KELIRU

Law 2 melarang membalik jurnal POSTED ke VOID. **Larangannya benar dan wajib
dipatuhi.** Tapi alasan yang tertulis di dokumen —

> *"the unique index is NOT status-filtered"*

— **salah**: indeks uniknya tak pernah ada (`idx_je_chain_seq` btree biasa).
Sebab sesungguhnya **trigger yang TERTUNDA sampai COMMIT**.

**Kenapa ini kelas tersendiri, bukan sekadar Law 34 biasa:**

Law 34 yang biasa kita catat = dokumen **menjanjikan pagar yang tak terpasang**.
Akibatnya orang merasa aman padahal tidak. Yang ini **kebalikan arah**: pagarnya
memang harus ada, aturannya benar, tapi **alasan yang tertulis mengirim orang
mencari obat di tempat yang salah.**

Terbukti pada kami sendiri, 12 Sep: dua sesi menghabiskan sekitar satu jam
berdebat **urutan pernyataan** (`balik-dulu` vs `posting-dulu`) karena alasan di
dokumen menunjuk ke indeks. Obat yang hampir diusulkan ke pemilik — *"tukar
urutan pernyataan"* — **tidak memperbaiki apa pun**: trigger tertunda tak pernah
melihat urutan. Kalau itu disetujui, ia akan ditandai selesai sementara cacatnya
utuh. **Lebih buruk daripada tak berbuat.**

**How to apply:** saat sebuah hukum menyebutkan SEBAB teknisnya, perlakukan
sebabnya sebagai **klaim terpisah** yang butuh bukti sendiri — bukan sebagai
bagian dari hukumnya. Hukum boleh dipatuhi tanpa percaya alasannya; **obat tidak
boleh dipilih dari alasan yang belum diukur.** Dan klaim yang terdengar spesifik
(`"unique index"`, `"status-filtered"`) justru **menghentikan pemeriksaan** —
kekhususan terbaca seperti bukti.

---

# KOREKSI & PENUTUPAN SEBAGIAN (12 Sep 2026, sore)

## Status berubah: arah 1 SUDAH DIKERJAKAN

Pemilik memilih arah 1. Terkirim sebagai `feb326b5` di `fix/law2-lima-jalur`:
kelima jalur berhenti membalik jurnal asli ke VOID.

**Yang TIDAK berubah:** trigger tetap `DEFERRABLE INITIALLY DEFERRED`, dan
**9 pasang nomor ganda + 2 hash pecah yang sudah ada tetap utuh** — menyembuhkannya
berarti menyunting jurnal POSTED (Law 2/3), dan itu masih putusan pemilik.
Arah 2 dan 3 masih terbuka; arah 3 masih akan GAGAL selama 9 pasang lama ada.

## Akar TERUJI — bukan lagi cuma dibaca dari definisi trigger

Gerbang dua sisi `scripts/gerbang_law2_dua_sisi.sql`, semuanya `ROLLBACK`,
nol baris menetap:

```
MERAH  (perilaku lama)  asli VOID 407 · pembalik 407  -> TABRAKAN
HIJAU  (perilaku baru)  asli POSTED 407 · pembalik 408 -> nol tabrakan
                        asli: POSTED + tertandai + reversed_at TERISI
sabotase reversed_at=NULL -> HANYA assertion baru yang memerah
kontrol: 0 sentinel menetap · max 406 · 9 pasang · 2 hash — tak bergerak
```

**BATAS yang harus ikut dikutip kalau angka ini dikutip:** ini membuktikan
**mekanisme basis data**, BUKAN jalur HTTP ujung-ke-ujung. Tak ada hibah izin
yang diambil untuk mendapatkannya.

## Prior art yang kutemukan TERLAMBAT

`stock_adjustments.py:1360` **sudah memperbaiki cacat yang sama lebih dulu**, dengan
catatan yang menamai mekanisme identik. `sales_invoices.py` menulis pasangan
`reversed_by_id + reversed_at` di **enam** tempat.

Akibatnya sunting pertamaku — `reversed_by_id` saja — akan menjadikan kelima
situs ini **varian ketujuh** dari perbaikan yang sama. Terukur saat itu:
158 jurnal ber-`reversed_by_id`, hanya 142 ber-`reversed_at`; **16 null itu
persis void beban**, yaitu jalur-jalur ini. `reversed_at` ikut bukan sebagai
tambahan, melainkan supaya bentuknya sama dengan rumah.

**Pelajarannya:** aku menulis lingkup "LIMA jalur" dari grep, lalu membangun
gerbang di atasnya, sebelum mencari apakah ada yang pernah menambalnya. Yang
seharusnya lebih dulu: cari tambalan yang SUDAH ADA untuk cacat yang sama.

## Dua jebakan yang memakan waktuku — ditulis supaya tak memakan waktu berikutnya

**1. `SET CONSTRAINTS ALL IMMEDIATE` BUKAN sekali-pakai.** Ia mengubah MODE
untuk sisa transaksi. Gerbang yang memakainya untuk memaksa penomoran jurnal
asli, lalu tidak mengembalikan `DEFERRED`, membuat trigger pembalik menyala di
**waktu-pernyataan** — sebelum flip VOID — dan membaca **408, bukan 407**.
Itu tampak seperti "akarnya salah". Bukan: **gerbangnya yang tak setia.**
Dua gerbangku memerah/tak-memerah ke arah berlawanan karena ini, dan sempat
membuatku hendak mencabut akar yang ternyata benar.

**2. Nihil dari saringan buruk bukan temuan.** `grep ... | grep -i journal`
untuk mencari jalur yang membalik ke VOID **melewatkan kelima situs** — barisnya
tidak memuat kata "journal". Kalau sebuah pencarian mengembalikan nol, periksa
saringannya sebelum menyimpulkan ketiadaan.

⚠️ Kalimat di bagian atas dokumen ini — *"menukar urutan pernyataan TIDAK
memperbaiki apa pun"* — **tetap benar untuk produksi** (trigger tertunda melihat
keadaan akhir transaksi). Tapi ia TIDAK berlaku di dalam transaksi yang sudah
di-`IMMEDIATE`-kan, dan di situlah gerbang mudah menipu dirinya sendiri.

## Akibat yang terlihat sesudah deploy

`void_count` di ringkasan jurnal akan **TURUN**: jurnal asli yang dibalik kini
terhitung POSTED, bukan VOID. Itu **bukan** kehilangan data dan bukan regresi —
`is_effective_journal()` menyaring lewat `reversed_by_id`, jadi **angka efektif
tidak bergeser**. Yang berubah hanya ember tempat mereka dihitung.
