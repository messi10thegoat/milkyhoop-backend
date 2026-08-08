# P0 SECURITY: kunci OpenAI ter-inline di bundle FE publik sejak 24 Juli 2026

**Tanggal:** 2026-08-04  **Severity:** **P0 — kunci rahasia terekspos publik**
**Status:** OPEN. **URUTAN DIUBAH 2026-08-06 atas keputusan owner: PROXY DULU, ROTASI MENYUSUL.**

> **Owner memutuskan TIDAK merotasi kunci sekarang.** Alasannya nol-downtime: merotasi lebih dulu
> mematikan input suara sampai proxy selesai. Urutan baru:
> **1) bangun `POST /api/voice/transcribe` → 2) FE pindah memanggil proxy → 3) hapus
> `REACT_APP_OPENAI_API_KEY` dari `.env.local` → 4) build+deploy → 5) BARU rotasi kunci lama.**
> Setelah langkah 4, kunci lama tak lagi ada di bundle mana pun, sehingga rotasi di langkah 5
> menutup paparan tanpa memutus fitur sedetik pun.
>
> **Konsekuensi yang diterima owner:** paparan berlanjut sampai proxy live. Ini keputusan sadar,
> bukan kelalaian. **JANGAN mengangkat ini lagi sebagai P0 yang menuntut rotasi segera** — yang
> P0 sekarang adalah **menyelesaikan proxy**, karena itulah yang membuka jalan rotasi.

## Fakta

- `.env.local` FE berisi `REACT_APP_OPENAI_API_KEY=sk-proj-xU75Ze…`
- CRA meng-**inline** semua variabel berprefiks `REACT_APP_` ke dalam bundle JavaScript saat build.
  Bundle itu disajikan publik. **Jadi kunci itu dapat dibaca siapa pun** yang membuka
  `https://milkyhoop.com/static/js/main.*.js`.
- Bukti `[CODE]`: string kunci ditemukan di
  - bundle live lama `main.5558c404.js` (disajikan publik sejak **2026-07-24**), dan
  - bundle baru `main.8f12c2eb.js` (deploy 2026-08-04).
- **Durasi paparan: sejak 24 Juli 2026, ≥11 hari.**
- Deploy hari ini **tidak memperburuk dan tidak memperbaiki** — kunci yang sama sudah publik sebelumnya.

## Akar masalah = ARSITEKTUR, bukan konfigurasi

Frontend memanggil OpenAI **langsung dari browser** dengan kunci ter-inline:

```
src/hooks/useVoiceInput.ts:134   const apiKey = process.env.REACT_APP_OPENAI_API_KEY;
src/hooks/useVoiceInput.ts:137   await fetch('https://api.openai.com/v1/audio/transcriptions', …)
```

Selama pola ini dipakai, **tidak ada kunci yang pernah bisa aman di situ.** Kunci apa pun yang
ditaruh di `REACT_APP_*` akan ikut ter-build ke bundle publik. **Mengganti kunci saja akan bocor lagi
di build berikutnya** — itu bukan perbaikan, hanya menunda.

**Fix yang benar:** proxy lewat backend. Kunci hidup di server (`.env`, nol di bundle); FE mengirim
audio ke endpoint milik kita sendiri yang terautentikasi, backend yang memanggil OpenAI.

## (a) Fitur yang terdampak — INPUT SUARA (voice-to-text), bukan Chatmode secara umum

Hook `useVoiceInput` (transkripsi Whisper) dipakai 3 komponen:
- `src/components/app/ChatPanel/ChatInput.tsx`
- `src/components/chat/VoiceMicButton.tsx`
- `src/components/chat/VoiceRecordingOverlay.tsx`

Chat berbasis teks **tidak** memakai kunci ini (chat jalan lewat backend `/api/v3/chat/*`).
Yang mati saat rotasi hanyalah **input suara**, bukan seluruh chat.

## (b) REACT_APP_* lain yang sensitif — TIDAK ADA

`.env.local` hanya memuat dua: `REACT_APP_API_URL` (kosong, URL relatif — tidak sensitif) dan
`REACT_APP_OPENAI_API_KEY` (sensitif). `src/` juga merujuk `REACT_APP_WS_URL` (URL, tidak sensitif).
**Hanya satu rahasia yang bocor.**

## (c) Endpoint proxy backend — BELUM ADA, harus dibangun (tapi fondasinya sudah ada)

- Nol router audio/voice/transcription di `backend/api_gateway/app/routers/`.
- **Namun** backend sudah punya `app/services/llm/openai_client.py` + `OPENAI_API_KEY` sendiri di
  `/root/milkyhoop-dev/.env`.
- **PENTING — kunci backend BERBEDA** (`sk-proj-Z3h_…`) dari kunci yang bocor (`sk-proj-xU75Ze…`).
  Konsekuensi: **merotasi kunci FE yang bocor TIDAK akan mematahkan backend** (OCR, chat, LLM router
  semuanya aman). Ini menyederhanakan rotasi secara signifikan.

Estimasi kerja proxy: satu endpoint `POST /api/voice/transcribe` (multipart audio → Whisper),
memakai `openai_client` yang sudah ada + advisory-lock tidak relevan (nol journal). Bukan pekerjaan besar.

## Konsekuensi rotasi — DITERIMA, bukan kejutan

**Merotasi kunci akan MEMATAHKAN input suara** di ketiga komponen di atas sampai proxy dibangun.
`useVoiceInput.ts:135` melempar `Error('REACT_APP_OPENAI_API_KEY not set')` bila kunci kosong — jadi
kegagalannya eksplisit, bukan senyap. Owner menerima konsekuensi ini; rotasi lebih mendesak daripada
ketersediaan input suara.

## Urutan tindakan yang disarankan

1. **Rotasi kunci sekarang** (owner, paralel) — hentikan paparan. Input suara mati; itu diterima.
2. Bangun `POST /api/voice/transcribe` di backend (kunci server-side).
3. Ubah `useVoiceInput.ts` memanggil endpoint itu; **hapus `REACT_APP_OPENAI_API_KEY` dari
   `.env.local`** supaya tak bisa ter-inline lagi.
4. Guard regresi: tambahkan cek build yang GAGAL bila `sk-` muncul di `build/static/js/*.js`.
   Sesuai draft Iron Law 33, guard itu wajib dibuktikan **bisa MERAH** (uji dengan kunci palsu)
   sebelum dipercaya.

## Catatan cakupan
Ditemukan saat rebuild FE (Bagian A) 2026-08-04. Di luar cakupan A dan B; difile terpisah agar tidak
mengganggu walkthrough. Deploy A tetap dilanjutkan karena tidak memperburuk keadaan.
