# POLA: "ENGINE DIBANGUN, WIRING TAK SELESAI" — enam instance, satu penyakit

**Tanggal:** 2026-08-06 **Kelas:** pola rekayasa (bukan bug tunggal)
**Kenapa difile sebagai pola:** enam kali kita menemukannya sebagai insiden terpisah dan
menambalnya satu per satu. Biayanya berulang karena **akarnya tak pernah dinamai**.

## Bentuk pola

Sebuah kapabilitas dibangun **lengkap dan benar** di lapisan dalam (DB, service, helper),
lalu **tidak pernah disambungkan** ke jalur yang dipakai pengguna. Hasilnya:

- Kode terlihat "sudah ada" saat di-grep → asumsi keliru bahwa fiturnya jalan
- Dokumen/skill mencatatnya sebagai selesai → sumber kebenaran ikut salah
- Nol test yang gagal → tak ada sinyal
- Baru ketahuan saat **manusia memakainya lewat UI**

## Instance terkumpul

| # | Engine yang dibangun | Wiring yang tak selesai | Ketahuan dari |
|---|---|---|---|
| 1 | `utils/idempotency.py` — `execute_idempotent()` lengkap dengan cache+replay, TTL, `ON CONFLICT` | **NOL pemakai.** Hanya `get_idempotency_key` dipakai, itu pun 1 router (`sales_invoices`). Lahir di `159da804`, nol commit lanjutan | audit idempotency 2026-08-06 |
| 2 | `applicable-deposits` endpoint + logika `match_type: spine` | 500 karena bind `customer_id` VARCHAR/UUID → panel FE kosong | walkthrough UI |
| 3 | `quotes.payment_bank_name/_account_number/_account_holder` kolom + form | detail endpoint tak me-map ketiganya → selalu null di UI | walkthrough UI |
| 4 | `tenant_config.revenue_recognition_policy` (invoice vs delivery) | **nol write path** — user tak bisa mengubahnya sama sekali | audit backlog |
| 5 | `team_invitations` + accept flow | `POST /team-members/invite` 400; tabel tak pernah di-INSERT → rantai accept orphan | audit backlog |
| 6 | `vendor_deposits` tabel + endpoint | write path 500 + akun salah kelas (1-10800 = PPN Masukan) | audit backlog |

Tambahan sejenis (wiring ada tapi salah sambung): FE mengirim header
`X-Idempotency-Key` sementara backend membaca `body.idempotency_key` — **dua ujung tak bertemu**.

## Kenapa berulang

1. **Selesai di lapisan dalam terasa seperti selesai.** Menulis `execute_idempotent` yang benar itu
   pekerjaan yang memuaskan; menyambungkannya ke 20 router itu membosankan dan tak terlihat.
2. **Grep memberi rasa aman palsu.** `grep idempotency` → banyak hit → "sudah ada". Tak ada yang
   memeriksa *hit-nya dipakai siapa*.
3. **Nol test yang menembus lapisan.** Unit test menguji engine (lulus); harness E2E memakai jalur
   backend yang tak melewati wiring FE. Keduanya hijau di atas fitur yang mati.
4. **Dokumen mencatat niat, bukan keadaan.** Skill/README menulis fiturnya seolah aktif.

## Penangkal yang diusulkan

**A. Definition of Done wajib memuat "pemakai pertama".** Sebuah engine tidak boleh di-merge tanpa
minimal satu jalur produksi yang memanggilnya — kalau belum ada, ia masuk sebagai *proposal*, bukan
*fitur*.

**B. Audit "kode mati" berkala:**
```bash
# helper yang didefinisikan tapi nol pemakai
grep -rn "^def \|^async def " backend/api_gateway/app/utils/ backend/api_gateway/app/services/ \
  | while read -r d; do fn=...; grep -rq "\b$fn(" --include=*.py . || echo "NOL PEMAKAI: $d"; done
```
Jalankan tiap sprint; setiap hit = kandidat instance pola ini.

**C. Uji dari ujung yang dipakai manusia.** Harness backend hijau tidak membuktikan wiring FE
tersambung (lihat blind-spot harness di `milkyhoop-e2e`). Walkthrough UI berkala adalah satu-satunya
yang menangkap kelas ini — **lima dari enam instance di atas ditemukan lewat UI, bukan test.**

**D. Saat menemukan instance baru, tambahkan ke tabel di dokumen ini** — supaya polanya terlihat
sebagai pola, bukan enam kejadian tak berhubungan.

## Catatan untuk owner
Ini bukan kritik terhadap kualitas kode — engine-enginenya justru ditulis benar. Masalahnya
**ekonomi perhatian**: bagian terakhir yang membuat fitur benar-benar hidup adalah bagian yang paling
tidak memuaskan untuk dikerjakan, dan tidak ada satu pun sinyal otomatis yang menagihnya.
