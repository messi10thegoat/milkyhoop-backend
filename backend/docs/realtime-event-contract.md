# Kontrak Event Realtime (Pembaruan Instan) — Tahap 1

**Status:** LIVE sejak BE `7e899db0` (2026-09-15). Sumber-tunggal untuk FE dan penambahan tabel berikutnya.
**Lingkup Tahap 1:** hanya tabel `sales_invoices`. Tabel lain & deteksi konflik = fase berikutnya.

---

## 1. Transport & koneksi

- **Endpoint:** `GET /api/events/stream` — Server-Sent Events (`text/event-stream`).
- **Auth:** `Authorization: Bearer <access_token>` via **fetch-stream** (ReadableStream), BUKAN `EventSource`
  (browser tak bisa set header di EventSource; token 7-hari TIDAK boleh masuk URL/log).
- **Satu koneksi per tab.** Middleware auth biasa memvalidasi token saat koneksi dibuka.
- **nginx:** `location = /api/events/stream` dengan `proxy_buffering off`, `proxy_set_header Connection ""`,
  `proxy_read_timeout 3600s`. (Di depan ada Cloudflare; heartbeat 20 dtk mengalahkan idle-cut CF ~100 dtk.)
- **Heartbeat:** komentar SSE `: keepalive\n\n` tiap **20 dtk**. Klien ABAIKAN.

## 2. Bentuk event (baris `data:` JSON)

| type | bentuk | kapan | perilaku klien yang DIHARAPKAN |
|---|---|---|---|
| `hello` | `{"type":"hello"}` | segera saat connect | picu **resync awal**: refetch tampilan aktif |
| `doc_changed` | `{"type":"doc_changed","tbl","id","op"}` | 1 dokumen berubah (commit) | refetch dokumen/list terkait `id`. `op`∈`INSERT`/`UPDATE`/`DELETE` |
| `bulk_changed` | `{"type":"bulk_changed","tbl"}` | >20 perubahan `tbl` dalam SATU transaksi (mis. import/posting massal) | refetch SELURUH list `tbl` (bukan per-dokumen) |
| `resync` | `{"type":"resync"}` | server reconnect ke DB (LISTEN putus→pulih) | refetch tampilan aktif (event saat putus tak terulang) |
| `revoked` | `{"type":"revoked","code"}` | server menutup koneksi (akses/token) | lihat tabel kode di bawah, lalu koneksi ditutup |

**Cakupan `tbl` saat ini** (bertambah per slice): `sales_invoices`, `sales_orders`, `bills`, `bill_payments_v2`, `receive_payments`, `customer_deposits`, `bank_transactions`, `bank_transfers`. Menyusul: `credit_notes` (G5), `quotes` (G6), lalu master/inventory/mfg/payroll.

**Event aplikasi (alokasi/penerapan).** Perubahan pada `receive_payment_allocations`, `customer_deposit_applications`, `credit_note_applications` memancarkan DUA event: parent (`receive_payments`/`customer_deposits`/`credit_notes`) DAN `sales_invoices` (id = faktur terdampak, `op:"UPDATE"`) — penerapan mengubah sisa tagihan faktur. Penerapan nota-kredit TAK menyentuh `sales_invoices`, jadi event faktur ini WAJIB agar tampilan faktur/AR ikut segar. Event parent yang `tbl`-nya belum dipetakan (mis. `credit_notes` sebelum G5) DIBUANG fail-closed.

**Payload MINIMAL by design:** hanya `{tbl,id,op}` — TANPA nomor/nama/nominal. Klien SELALU ambil ulang
lewat API biasa (izin ditegakkan ulang saat refetch). Ini pertahanan: kebocoran otz tak membocorkan data.

### Kode `revoked`

| code | arti | perilaku klien |
|---|---|---|
| `TOKEN_EXPIRED` | klaim `exp` token terlewati saat streaming | `refreshAccessToken()` (single-flight) → BUKA ULANG stream dgn token terbaru |
| `MEMBERSHIP_INACTIVE` | keanggotaan tenant dinonaktifkan/dicabut | picu event `milkyhoop:akses-nonaktif` yang sudah ada |
| `USER_NOT_FOUND` | akun `"User"` dihapus | logout |
| `UNAUTHENTICATED` | tak ada `request.state.user` (mestinya sudah 401 di middleware) | logout/refresh |

## 3. Pola reconnect klien

- Stream berakhir / error / `TOKEN_EXPIRED` → `refreshAccessToken()` (single-flight) → buka ULANG dgn token
  terbaru dari localStorage, dengan backoff.
- `resync` → refetch tampilan aktif.
- Tutup koneksi saat **logout** dan saat **ganti tenant** (full reload).
- Autz per-event ditegakkan di SERVER (tenant sama + modul READ). Klien tak perlu memfilter; cukup refetch
  saat menerima event untuk `tbl` yang sedang ditampilkan.

## 4. Model otorisasi (server)

- Event hanya dikirim ke koneksi yang **`tenant_id` sama** DAN punya **READ** untuk modul `tbl`.
- Peta `tbl → modul` (`services/realtime.py:TBL_MODULE`): `sales_invoices → sales_invoice`.
  Cek izin memakai `PolicyEngineClient.can(ctx,'R',modul)` — fungsi yang SAMA dengan middleware
  (termasuk `normalize_module_name`: `sales_invoice → INVOICE`, bypass OWNER, cek membership). **Satu sumber**,
  hindari drift dengan `permission_middleware` PROTECTED_ROUTES.
- `resync`/`revoked`/`hello` = pesan kontrol, TIDAK difilter per-modul.
- **AR/AP caches DIKECUALIKAN sengaja:** `accounts_receivable`/`accounts_payable` = cache turunan (Law 16), bukan sumber; tampilan piutang/hutang segar lewat event `sales_invoices`/`bills`, bukan tabel cache. Ledger (`journal_lines`, `inventory_ledger`) & log juga dikecualikan.
- **Fail-closed:** `tbl` yang TAK ada di `TBL_MODULE` DIBUANG — tak lagi disiarkan tanpa filter. Tabel baru tetap gelap sampai `TBL_MODULE` memetakannya.

## 5. Pencabutan (severance)

- Recheck user+membership tiap **60 dtk** dan saat **token exp**.
- Trigger `NOTIFY membership_changed` pada `user_tenant_roles` → koneksi terkait di-recheck **SEGERA**
  (terukur ~8 md) → kirim `revoked` + tutup.
- Perubahan izin via **endpoint** (Kelola Akses/override) meng-invalidate cache (`team_members.py`
  `invalidate_user_cache`) sehingga recheck berikut melihatnya. Perubahan `role_permissions` lewat SQL/migrasi
  menuntut restart (cache peran tanpa pembatalan — aman karena tak ada endpoint produk menulis `role_permissions`).

## 6. Internal server (ringkas)

```
INSERT/UPDATE/DELETE sales_invoices  (COMMIT)
      │  trigger trg_notify_doc_changed (AFTER, migrasi V259)
      ▼  pg_notify('doc_changed', {tenant_id,tbl,id,op})   ← transaksional: rollback = nol event
RealtimeHub (services/realtime.py): 1 koneksi asyncpg khusus LISTEN PER WORKER (application_name=mh_realtime_listen; 2 worker -> 2 koneksi)
      │  coalesce per (tenant,tbl,id) ~150ms; burst >20/(tenant,tbl) → 1 bulk_changed
      │  termination-listener → reconnect SEGERA saat koneksi mati; keepalive 25s utk half-open; resync pasca-reconnect
      ▼  fan-out per-worker (tiap worker LISTEN sendiri; pg_notify BROADCAST ke semua worker; Redis TIDAK diperlukan selama 1 replika)
GET /api/events/stream (routers/events.py): filter tenant+modul, heartbeat 20s, recheck 60s, tutup saat exp
```

- **Transaksional:** `pg_notify` hanya terkirim saat COMMIT → rollback = 0 event (terukur).
- **Header-only:** trigger HANYA di tabel header dokumen + `user_tenant_roles`. TIDAK di `journal_lines`/`journal_entries`.

## 7. Menambah tabel baru (fase berikutnya)

1. Migrasi: pasang `trg_notify_doc_changed` (fungsi `notify_doc_changed()` sudah generik via `TG_TABLE_NAME`)
   pada tabel HEADER dokumen baru (bukan tabel baris/`journal_*`).
2. `services/realtime.py:TBL_MODULE`: tambah `<tabel> → <modul middleware>` (harus cocok PROTECTED_ROUTES).
3. Gerbang dua sisi ulang: commit→event, tenant lain→0, peran tanpa READ→0, rollback→0.

## 8. Batas yang diketahui

- **uvicorn `--workers 2`, 1 replika** (KOREKSI 15 Sep — bukan 1 worker; `docker top` konfirmasi). Hub in-memory
  memadai BUKAN karena worker tunggal, melainkan karena **pg_notify broadcast ke SEMUA koneksi LISTEN** (tiap
  worker start hub+LISTEN sendiri via lifespan per-proses) -> tiap doc_changed tiba di kedua worker, fan-out ke
  subscriber worker itu. N worker = N koneksi LISTEN. Skala HORIZONTAL (multi-REPLIKA/mesin) TETAP wajib Redis
  pub/sub: pg_notify lintas-worker OK, tak lintas-DB. (recheck-stampede & fan-out skala besar juga -> Redis.)
- **Token 7 hari (HS256).** Severance nyata via NOTIFY + recheck 60s (exp jarang kena di tengah sesi).
- **Deteksi konflik edit (Tahap 3):** `updated_at` ada di 10/10 tabel dokumen inti sebagai token If-Match; belum dibangun.

## 9. Verifikasi (Tahap 1, semua GREEN)

commit→event 0.068s (edge 0.16s) · rollback→0 · lintas-tenant→0 · filter modul → 0/1 · nonaktif→revoked 8ms ·
LISTEN mati→reconnect+resync 1.09s · 100/1tx→1 bulk (edge juga) · token exp→TOKEN_EXPIRED 5.6s · tanpa READ→0 ·
edge ≥5min (315s, heartbeat 20.0s) · 20 stream: latensi tak naik (p95 10.5→10.3ms), koneksi +7 bukan +20.
