# T176 — kartu terender DUA KALI pada jalur peringatan T174 (KOSMETIK)

Tanggal uji: 2026-08-29. Diukur lewat https://milkyhoop.com (UA browser + Origin + Referer).
Tenant uji: kaos-biru-konveksi. Prefix: "Kaos Uji T176b". Sesi BARU tiap probe (UUID).

## SEBAB — file:line

- [CODE] `frontend/web/src/components/app/ChatPanel/components/MessageRenderer.tsx:348`
  `const showNarrative = msg.content && msg.content !== directData.confirmation_table;`
  (kembar di `:395` untuk render_target='artifact')
- [CODE] `backend/.../unified_agent/tool_executor.py:2145` `"content": confirmation_table`
  dan `:2159` `"confirmation_table": confirmation_table` — SATU variabel yang SAMA.
  Karena itu kartu tunggal biasa: `content == confirmation_table` -> showNarrative FALSE -> nol tabel.
- [CODE] T171 (`tool_executor.py:2168-2200`): `response_data["content"] = _t171_teks`
  -> MENIMPA. Kesamaan putus DENGAN SENGAJA, narasi menyala, penanda "Barang k dari N" + progress tampil.
- [CODE] T174 SEBELUM (`orchestrator.py:4346-4355`): peringatan DISAMBUNG ke depan `content`
  (`(...) + _t174_content`). Kesamaan putus TANPA SENGAJA -> FE merender confirmation_table
  lagi sebagai narasi, di atas kartu yang isinya sama. Itulah duplikasinya.

Perbedaan T171 vs T174 = MENIMPA vs MENYAMBUNG.

## PERBAIKAN (satu situs)

`orchestrator.py` ~4346: kalau `content == data.confirmation_table` -> TIMPA dengan peringatan saja.
Kalau bukan (narasi slide T171) -> tetap disambung. `showNarrative` TIDAK disentuh.

## GATE

### M1 — MERAH (sebelum, commit f7bf517e)
[HTTP] sesi 38bd1c12-627f-4b03-8b9b-192f433e367d, len_text=421 / len_table=149
WARN_IN_TEXT=True TABLE_IN_TEXT=True
```
⚠️ Pesan ini sepertinya memuat beberapa barang, tapi saya cuma berhasil menyusun satu kartu — periksa namanya baik-baik.
Yang tidak tersusun: «Kaos Uji T176b Alfa, Kaos Uji T176b Beta, Kaos Uji T176b Gama»
Kalau memang beberapa, kirim ulang sisanya bernomor (1. … 2. …).

### Buat Barang/Jasa

| Field | Value |
|-------|-------|
| Nama | Kaos Uji T176b |
| Tipe | persediaan |
| Satuan | pcs |
| Harga Jual | Rp 45.000 |
```
[LOG] bukti jalur:
`[T144_BULK] items string gagal di-parse: err=Expecting value: line 1 column 1 (char 0) len=61 head='Kaos Uji T176b Alfa, Kaos Uji T176b Beta, Kaos Uji T176b Gama'`

### M1 — HIJAU (sesudah, commit 4b706f45) — 4 dari 6 probe kena jalur T174
[HTTP] sesi be4584f3 / 067a35bd / 04676998 / 6244988d — len_text=270, TABLE_IN_TEXT=False
```
⚠️ Pesan ini sepertinya memuat beberapa barang, tapi saya cuma berhasil menyusun satu kartu — periksa namanya baik-baik.
Yang tidak tersusun: «Kaos Uji T176b Alfa, Kaos Uji T176b Beta, Kaos Uji T176b Gama»
Kalau memang beberapa, kirim ulang sisanya bernomor (1. … 2. …).
```

### G1 — HIJAU (pagar terpenting)
[HTTP] sesi 90f80aaf, 66f58852 — `progress={'current':1,'total':3}`, TABLE_IN_TEXT=False,
teks memuat "Barang 1 dari 3: **Kaos Uji T176b Alfa**". Penanda slide T171 UTUH.

### G2 — HIJAU
[HTTP] sesi fe38f0b1 — TEXT_EQ_TABLE=True (len 181 == 181) -> showNarrative FALSE -> nol narasi.

### G3 — TIDAK TERBUKTI (lihat bawah)

### G4 — HIJAU
[HTTP] sesi c125e75c — "Pesan ini memuat 12 barang sekaligus ... Tidak ada satu pun yang saya simpan."
[LOG] `[T144_BULK_BATAS] 12 barang dalam satu pesan (batas 10) -- minta dipecah, nol yang disimpan`

## DEPLOY
- master sebelum: f7bf517e570aeac9a8a618281db365f4b2f219f1
- master sesudah: 4b706f45751283c97fcc844f9894f390c4d36ddb
- StartedAt sebelum: 2026-08-29T03:25:36.418470933Z
- StartedAt sesudah: 2026-08-29T06:50:44.340485715Z (healthy)
- Diferensial perilaku: len_text 421 -> 270 pada stimulus yang sama.

## DIFF GRAPGRAP (dua ujung) — NOL SENTUHAN
| | sebelum | sesudah |
|---|---|---|
| products | 6 | 6 |
| pending_actions | 1321 | 1321 |
| journal_entries | 16 | 16 |
| inventory_ledger | 2 | 2 |

kaos-biru-konveksi: journal_entries 11 -> 11, inventory_ledger 3 -> 3 (nol jurnal, nol stok).
pending_actions 923fc93b: masih ada (1).

## OBJEK LAHIR & DIBERSIHKAN
products kaos-biru 50 -> 52, keduanya soft-delete:
- 3e70c9fc-0ddd-4555-bcb1-ae23df97e81c "Kaos Uji T176b G3a"
- c7d7f4db-c7d6-4f70-bba2-7726c247caaa "Kaos Uji"
Kontrol negatif: UPDATE diulang -> UPDATE 0.
pending_actions lahir di kaos-biru: 17 (tak dihapus; kartu kedaluwarsa, nol efek pembukuan).
pending_action id jalur T174 HIJAU: ec494dd0, 56a9c4f0, 57d88e62, 848d1dd7.
pending_action id jalur T174 MERAH: 0c464ce2.

## YANG TIDAK TERBUKTI
1. **G3 (ringkasan penutup T173) TIDAK diuji.** Harness confirm skrip saya tidak
   mereproduksi auto-send FE antar-slide; rangkaiannya putus di ronde 2 dan malah
   membuat barang bernama "Kaos Uji" (sudah dibersihkan). Yang ada hanya [CODE]:
   ringkasan T173 hidup di `unified_chat.py:3733`, di luar hunk yang diubah.
2. **Nomor baris FE berlaku untuk worktree lokal Mac.** Bundle produksi adalah image
   BAKED; klaim bahwa `showNarrative` di produksi identik bersandar pada kesetaraan itu,
   bukan pada pembacaan bundle terpasang.
3. **Duplikasi hilang DI LAYAR belum dilihat.** Seluruh bukti di atas ada di lapisan
   `text`/`confirmation_table` HTTP. Ronde lalu seluruh gate hijau di lapisan `text`
   dan tetap melewatkan duplikasi ini. Owner masih perlu melihat sendiri.
4. Kembar `showNarrative` di `MessageRenderer.tsx:395` (render_target='artifact')
   tidak diuji — jalur T174 create_item memakai 'inline'.
5. Rollback (`git revert` 4b706f45 + restart) TIDAK dieksekusi; bahwa duplikasi
   kembali setelah revert belum dibuktikan.

## TERLIHAT, DILEWATI
- Model kadang mengisi `item_type` "persediaan" (bukan "goods") pada jalur bulk —
  di luar lingkup T176.
