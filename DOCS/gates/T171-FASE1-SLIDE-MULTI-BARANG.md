# T171 FASE 1 — SLIDE MULTI-BARANG — ARTEFAK GATE

Tanggal uji: **2026-08-29** (dijalankan 2026-08-28T17:20Z–17:55Z UTC)
Tenant: `kaos-biru-konveksi` SAJA. Prefix uji: `Kaos Uji T171`.
Basis: master `aa8c31b7`. Branch: `feat/t171-slide`.
Diukur di gateway TERISOLASI `:8002` (container `mh-t171-gw`, bind-mount
`/root/mh-t171/backend/api_gateway`), dibandingkan terhadap `:8001` (master).

## Q0 — APAKAH BUTUH PERUBAHAN FE? TIDAK. [CODE]

FE **NOL berubah**. Tiga kontrak yang sudah ada dipenuhi backend:

1. auto-"lanjut" dua arah — `useActionMode.ts:344` membaca
   `directData.workflow_continuation`; `ChatPanel/index.tsx:287` (Confirm) dan
   `:328` (Batal) dua-duanya mengirim `"lanjut"`.
   -> backend menyetel `data.workflow_continuation = True` pada SETIAP slide,
      termasuk slide TERAKHIR (lanjut terakhir itulah yang memunculkan ringkasan).
2. bilah progress — `MessageRenderer.tsx:347`
   `showNarrative = msg.content && msg.content !== directData.confirmation_table`;
   `progress` dirender di `:355-362` DI DALAM showNarrative.
   -> backend mengisi `content` dengan NARASI (bukan tabel), sehingga
      `content !== confirmation_table` -> showNarrative TRUE -> progress muncul.
      Ini sebabnya `content` TIDAK boleh berisi tabel: RED M1 membuktikan
      `text == confirmation_table` -> nol prosa, nol penanda.
3. pensil MATI — `editable:false` per-field pada review_card; jalur yang sudah ada.
   NOL sentuhan `payloadOverridesRef` -> T160 tak tersentuh.
4. kalimat "ditinggal di tengah" tiba sebagai pesan TEXT biasa di riwayat.
   `useChatHistory.transformMessages` memakai `msg.id` sebagai string biasa
   (nol parse UUID) dan mengurutkan by timestamp -> id sintetis aman.

## GATE MERAH (terhadap master `aa8c31b7`, via milkyhoop.com)

M1 MERAH — lima barang -> SATU kartu tabel:
  `message_type=DIRECT_ACTION_PREVIEW`, `pending_action_id=fad3d57e-...`
  `text` byte pertama = `###`; `text == data.confirmation_table` (nol prosa)
  `data.progress = null`; `data.workflow_continuation = null`
  `review_card.title = "Buat 5 Barang/Jasa"`
  `review_card.header` = 5 elemen dengan `"key":"items"` (SKEMA M1), semua `editable:false`

M2 MERAH — confirm kartu bulk:
  `text = "5 dari 5 barang berhasil didaftarkan: ... (baris 1..5)."`
  `data.entity_id = 8d6497bd-3ace-4e52-a51c-db545cee4ad1` -> **SATU id untuk LIMA produk**
  Nol cara melewati satu barang: Batal membatalkan kelimanya.

M4 MERAH — satu `pending_actions` -> satu `expires_at` untuk kelima calon.

## GATE HIJAU (`:8002`, kode T171)

M1 HIJAU — kalimat pembuka + kartu 1/5:

```
Ada 5 barang di pesan ini. Saya tampilkan satu per satu supaya tiap barang bisa dicek — dan dilewati — sendiri-sendiri.

1. Kaos Uji T171 (Size XS-XL) — Jual Rp 50.000 · Beli Rp 30.000 · pcs · persediaan
2. Kaos Uji T171 (Size 2XL) — Jual Rp 55.000 · Beli Rp 33.000 · pcs · persediaan
3. Kaos Uji T171 (Size 3XL) — Jual Rp 60.000 · Beli Rp 36.000 · pcs · persediaan
4. Kaos Uji T171 (Size 4XL) — Jual Rp 65.000 · Beli Rp 39.000 · pcs · persediaan
5. Kaos Uji T171 (Size 5XL) — Jual Rp 70.000 · Beli Rp 42.000 · pcs · persediaan

Barang 1 dari 5: **Kaos Uji T171 (Size XS-XL)**
```

  `data.progress = {current:1, total:5}` · `data.workflow_continuation = True`
  `review_card.title = "Buat Barang/Jasa"` (bukan "Buat 5 ...")
  `review_card.header` kunci = `name`/`item_type`/`base_unit`/`sales_price`/`purchase_price`
  = **SKEMA G1**, semua `editable:false` (pensil MATI, slide batch)

M2 HIJAU — Buat-Batal-Buat-Buat-Buat (5 slide, nama R3):
  slide 1..5 lahir SATU PER SATU, `progress` 1/5..5/5, `wfc=True` di tiap slide
  **4 produk lahir [SQL], masing-masing `entity_id` SENDIRI:**

```
6fb6fc05-7ec8-41f6-a98a-8002e2e56d4f  Kaos Uji T171 R3 (Size XS-XL)  50000/30000 pcs goods
14273f34-f565-44ad-8f25-3c77b933014b  Kaos Uji T171 R3 (Size 3XL)    60000/36000 pcs goods
ec9f3117-65cb-48d5-973b-e6d87cdfabef  Kaos Uji T171 R3 (Size 4XL)    65000/39000 pcs goods
65b8bf3c-6de0-488e-aca9-5a5e1d8df79a  Kaos Uji T171 R3 (Size 5XL)    70000/42000 pcs goods
```

  1 dilewati: `Kaos Uji T171 R3 (Size 2XL)` -> `pending_actions.status='CANCELLED'`
  Ringkasan penutup (teks apa adanya):

```
Selesai. 4 dari 5 barang dibuat. Dibuat: Kaos Uji T171 R3 (Size XS-XL) · Kaos Uji T171 R3 (Size 3XL) · Kaos Uji T171 R3 (Size 4XL) · Kaos Uji T171 R3 (Size 5XL) — Dilewati: Kaos Uji T171 R3 (Size 2XL)
```

  Ringkasan DIBACA DARI `pending_actions` (status per `_batch_id`), bukan akumulator.
  Kolom gagal terbukti bisa menyala: run sebelumnya (nama bentrok unique index)
  menghasilkan `... — Dilewati: (2XL) — Gagal: (XS-XL)`.

  **assertion `[RESOLVE_ITEM] = 0` selama M2 [LOG]** — `docker logs | grep -c` = **0**
  **KONTROL POSITIF** (`create_sales_invoice`, BUKAN `create_bill`):
    `[RESOLVE_ITEM] _resolve_item dipanggil, fragment='Kaos Uji T171 R3 (Size 3XL)'` -> **1**
    dan jurnalnya benar (Dr Piutang 120.000 = 2 x 60.000) -> pengukurnya BISA menyala.

M3 HIJAU — tutup panel di 3/5, buka lagi (`GET /sessions/{id}/messages`):

```
2 barang belum sempat ditampilkan: Kaos Uji T171 M3 (Size 4XL) · Kaos Uji T171 M3 (Size 5XL). Ketik 'lanjut'.
```

  Yang hidup = DAFTARNYA. Hanya 1 `DIRECT_ACTION_PREVIEW` di riwayat, bukan N kartu.

M4 HIJAU — TTL per slide [SQL] (`created_at` -> `expires_at`, TTL 5 menit):

```
idx  nama                            status     created   expires
1    Kaos Uji T171 R3 (Size XS-XL)   COMPLETED  17:26:49  17:31:49
2    Kaos Uji T171 R3 (Size 2XL)     CANCELLED  17:26:50  17:31:50
3    Kaos Uji T171 R3 (Size 3XL)     COMPLETED  17:26:50  17:31:50
4    Kaos Uji T171 R3 (Size 4XL)     COMPLETED  17:26:50  17:31:50
5    Kaos Uji T171 R3 (Size 5XL)     COMPLETED  17:26:51  17:31:51
```

  LIMA baris `pending_actions`, LIMA `expires_at`. Slide ke-3 punya sisa waktu PENUH.

M5 HIJAU (tetap) — 14 barang:

```
Pesan ini memuat 14 barang sekaligus, sementara saya hanya sanggup memproses 10 barang dalam satu kartu. Tidak ada satu pun yang saya simpan. Mohon dipecah jadi beberapa pesan, masing-masing paling banyak 10 barang.
```

  Batas 10 (`T144_BATAS_ITEM`, gerbang orchestrator `:1413-1421`) TIDAK digeser:
  pemecahan slide terjadi di `_execute_propose_direct`, jauh di HILIR gerbang itu.

M6 HIJAU — kalimat pembuka memuat 5 dari 5 nama, tidak terpotong (lihat M1).

## HIJAU-TETAP-HIJAU — diff `:8001` (master) vs `:8002` (T171)

G1 SATU barang -> **NOL BEDA**. header per-field, `name`/`base_unit`/`sales_price`/
   `purchase_price` `editable:true` (**pensil HIDUP**), `item_type` false (enum,
   pra-ada), `progress=null`, `workflow_continuation=null`, nol kalimat pembuka.
G2 `create_bill` 2 baris -> **NOL BEDA**. `review_card.items` = 2 elemen,
   `journal_preview` = `Dr Persediaan Barang Dagangan 650.000 / Cr Hutang Usaha 650.000`,
   TETAP satu kartu.
G3 `create_quote` 2 baris -> **NOL BEDA**. `quote_number` MASIH berisi kalimat user
   mentah ("untuk toko melati, kaos hitam 24s 10 pcs harga 60000 dan kaos biru 30s
   5 pcs harga 70000") = keadaan TERUKUR Fase 0; `"description":"Item"` TIDAK
   reproduksi (items = "Kaos Hitam 24s"/"Kaos Biru 30s").
G4 `create_customer` -> beda SATU field (`company_name`) pada satu pasangan run.
   **Bukan sebab kode**: 3x3 run silang membuktikan nondeterminisme LLM HADIR DI
   KEDUA port — `:8001` = [ada, tidak, ada], `:8002` = [ada, tidak, tidak].
G5 kalimat non-pendaftaran -> **NOL BEDA**. `message_type=TEXT`,
   `pending_action_id=null`.

## PAGAR KESELAMATAN

Jurnal: `create_item` tanpa stok awal = NOL jurnal.
  `journal_entries` baru untuk kaos-biru selama sesi = **0**; `inventory_ledger` baru = **0**.
  KONTROL POSITIF pengukurnya: total historis kaos-biru = 11 jurnal / 3 ledger (>0).
  `[CODE]` `create_item` -> `creates_journal=False`. NOL sentuhan
  `creates_journal` / `opening_stock` / `inventory_ledger`.

Bentuk master `kaos-biru-konveksi`:
  AWAL  total=15  hidup=2
  AKHIR total=31  hidup=2   (16 baris uji, SEMUA soft-deleted)

**DIFF grapgrap-manado (AWAL DAN AKHIR) = NOL:**
  products total   4 -> 4
  products hidup   4 -> 4
  pending_actions  1312 -> 1312

Pembersihan barang uji: soft-delete + `RETURNING` = 7 baris (run terakhir),
  **KONTROL NEGATIF** (ulang perintah yang sama) = `UPDATE 0`.

## YANG DIBUBARKAN

- `build_confirmation_table` cabang bulk -> diganti log `[T171_SISA_BULK]`
- `build_review_card_payload` cabang bulk -> dihapus (kartu selalu skema G1)
- loop `POST /api/items` di `unified_chat.py` -> dihapus; jalur tunggal
  (yang sama dengan G1 dan dengan form UI) + log `[T171_SISA_BULK]`
- dua blok kalimat-sukses/galat bulk yang bergantung `_t144_baris` -> dihapus
- pembantu render baris `t144_baris_teks` / `t144_masalah_baris` /
  `t144_baris_bisa_dibuat` / `t144_peringatan` -> **DIPERTAHANKAN dan DIPAKAI ULANG**

## KEPUTUSAN DESAIN YANG PERLU DIKETAHUI

Antrean sisa disimpan di **`pending_actions.action_plan`** (`_batch_queue`),
BUKAN di `chat_session_state.document_context`. Sebabnya terukur:
`StateUpdateHooks.after_propose` menulis ulang `document_context` SETELAH tiap
propose, jadi antrean di sana bisa tertimpa diam-diam. `action_plan` (jsonb)
tak disentuh siapa pun setelah INSERT. **NOL perubahan skema.**

WASPADA terukur [SQL]: kolom `pending_actions.conversation_id` BERISI SESSION
ID, bukan conversation_id — `_execute_propose_direct` menulis `self.session_id`
ke kolom itu. Pencarian batch mencocokkan KEDUANYA.

## YANG TIDAK TERBUKTI

1. **Belum diukur DI LAYAR (kelas [UI]).** Seluruh gate ini [HTTP]/[SQL]/[LOG].
   Rantai auto-"lanjut" dibuktikan [CODE] + disimulasikan lewat HTTP (kirim
   literal "lanjut"), BUKAN lewat browser. Law 33 / e2e-ui-walkthrough:
   harness hijau != alur UI jalan.
2. **Slide 2..N tidak tersimpan di `chat_messages`.** `_advance_item_slide`
   mengembalikan kartu LANGSUNG dari gerbang "lanjut" — persis seperti jalur
   multi-bukti (`_advance_document_queue`). Akibatnya riwayat hanya memuat
   kartu slide 1. Itu justru yang membuat M3 benar (daftar, bukan N kartu),
   tapi ia BELUM diputuskan sebagai desain — ia DIWARISI.
3. **Belum diuji: dua rentetan slide serentak di satu tenant, sesi berbeda.**
4. **Belum diuji: slide kedaluwarsa (>5 menit) lalu "lanjut".** Status EXPIRED
   dihitung sebagai "dilewati" di ringkasan — dibaca dari kode, bukan dijalankan.
5. **Belum di-deploy ke produksi.** `:8002` berbagi `milkydb` dan memanggil
   `POST /api/items` lewat base_url yang sama; ia mengisolasi KODE CHAT, bukan data.
6. **G4 nondeterminisme LLM tidak dihilangkan, hanya dikarakterisasi** (3x3 run).
