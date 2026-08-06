# P1: `@app.on_event("startup")` TIDAK PERNAH BERJALAN — poller Tier-3 mati sejak awal

**Tanggal:** 2026-08-06 **Severity:** P1 (fitur dianggap aktif, nyatanya tak pernah hidup)
**Status:** OPEN untuk isi handler-nya; wiring baru sudah dipindah ke tempat yang benar
**Kelas bukti:** `[CODE]` + `[LOG]` produksi, bukan inferensi

## Fakta

`main.py:252` membuat app dengan **`lifespan=prisma_lifespan`**.
`main.py:326` mendefinisikan **`@app.on_event("startup") async def startup_event()`**.

Ketika parameter `lifespan=` dipakai, **Starlette mengabaikan seluruh `@app.on_event("startup")`
dan `@app.on_event("shutdown")`**. Handler itu tak pernah dieksekusi.

### Bukti dari log produksi (bukan teori)

`docker logs milkyhoop-dev-api_gateway` menyimpan sejak **2026-07-24**:

| String | Asal | Kemunculan |
|---|---|---|
| `"API Gateway starting up..."` | `startup_event()` | **0** |
| `"Connected to Auth Service successfully"` | `startup_event()` | **0** |
| `"Bot memory summary poller started"` | `startup_event()` | **0** |
| `"Prisma connected."` | `prisma_lifespan` | ✅ muncul |
| `"PolicyEngine initialized"` | `prisma_lifespan` | ✅ muncul |

Pemisahannya bersih: **semua** yang di `prisma_lifespan` berjalan, **nol** yang di `startup_event`.

## Yang ikut mati

1. **Poller ringkasan Tier-3** (`summary_poller_loop`) — memory proyek mencatatnya sebagai
   "poller berjalan tiap 5 menit". **Tidak pernah berjalan.** Setiap diagnosa yang mengasumsikan
   ia hidup perlu ditinjau ulang (mis. `chat_sessions.final_summary` yang kosong bukan bug
   generator — pollernya memang tak pernah start).
2. **`auth_client.connect()` saat startup** — auth tetap berfungsi, kemungkinan karena koneksi
   dibuat lazy pada panggilan pertama. Artinya kita kehilangan warm-up, bukan fungsionalitas.
   Perlu konfirmasi terpisah.

## Kenapa tak pernah ketahuan

Tidak ada yang gagal dengan keras. Handler yang tak dipanggil **tidak melempar apa pun** — ia hanya
tak ada. Grep menemukan kodenya, review membaca kodenya, dokumen mencatatnya sebagai fitur.
Satu-satunya gejala adalah **ketiadaan** baris log, dan ketiadaan tidak menarik perhatian siapa pun.

Ini instance lain dari pola **"engine dibangun, wiring tak selesai"**
(`2026-08-06-pattern-engine-built-wiring-unfinished.md`) — dengan varian yang lebih halus: wiring-nya
**pernah benar**, lalu mati diam-diam saat app dipindah ke `lifespan=`. Nol test yang menangkapnya
karena nol test yang menegaskan "poller harus start".

## Yang sudah dikerjakan

- Poller pembersih idempotency (Law 14) dipasang di **`prisma_lifespan`**, bukan `startup_event`.
  Terverifikasi start: `Idempotency cleanup poller started` + `[IDEM_CLEANUP] tick done: deleted=0`.
- `startup_event` diberi komentar peringatan keras + rujukan ke tiket ini, supaya tak ada yang
  menambah kode startup ke handler mati.

## Yang BELUM dikerjakan (butuh keputusan)

**Isi `startup_event` belum dipindahkan.** Memindahkan `summary_poller_loop` ke `prisma_lifespan`
akan **menghidupkan poller yang belum pernah berjalan di produksi** — perilaku baru, beban baru,
dan jalur kode yang belum pernah diuji di bawah beban nyata. Itu keputusan produk, bukan
pembersihan mekanis. Opsi:
1. Pindahkan + pantau (fitur akhirnya hidup)
2. Hapus (kalau Tier-3 sudah tak diinginkan)
3. Biarkan mati + dokumentasikan (utang tercatat)

## Pelajaran umum

**Kalau sebuah framework menyediakan dua mekanisme untuk hal yang sama, dan memakai satu
mem-*disable* yang lain diam-diam, itu jebakan.** Cari tanda-tandanya di tempat lain:
`on_event` vs `lifespan`, middleware vs dependency, dua konfigurasi logging.

**Uji yang seharusnya ada:** smoke test startup yang menegaskan baris log tertentu muncul.
"Poller start" adalah klaim yang bisa diuji; selama ini ia hanya diasumsikan.
