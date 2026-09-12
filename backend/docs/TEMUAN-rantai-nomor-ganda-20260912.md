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
