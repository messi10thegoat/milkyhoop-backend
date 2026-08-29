# T174 FASE 1 — Gate: kegagalan parse `items` tidak lagi berhenti diam

Situs: `backend/api_gateway/app/services/unified_agent/orchestrator.py`
- `_t144_normalisasi_items` (blok `except`) — kini MENGEMBALIKAN string mentah yang gagal di-parse
- return `DIRECT_ACTION_PREVIEW` create_item — kartu TETAP terbit, DITAMBAH satu kalimat

Diukur lewat https://milkyhoop.com (bukan :8001/:8002), tenant `kaos-biru-konveksi`,
sesi BARU tiap probe, prefix "Kaos Uji T176".

## M1 — MERAH (sebelum patch, master c4310ff5)

Pesan:
`Daftarkan Kaos Uji T176 A dan Kaos Uji T176 B dan Kaos Uji T176 C, harga jual 100000 harga beli 60000`

[LOG] bukti berada di jalur yang benar (container `milkyhoop-dev-api_gateway`):
```
[T144_BULK] items string gagal di-parse: err=Expecting value: line 1 column 1 (char 0) len=32 head='Kaos Uji T176 B, Kaos Uji T176 C'
```

[HTTP] balasan APA ADANYA (`text`), NOL kalimat — B dan C menguap tanpa jejak di layar:
```
### Buat Barang/Jasa

| Field | Value |
|-------|-------|
| Nama | Kaos Uji T176 A |
| Tipe | persediaan |
| Satuan | pcs |
| Harga Jual | Rp 100.000 |
| Harga Beli | Rp 60.000 |
```
message_type=DIRECT_ACTION_PREVIEW  pending_action_id=59f15dfd-640b-4fd1-9565-5ef945433af4
sesi=7746ac94-7c3d-4cc5-a155-f4b93765f867

Gate merah terpicu pada percobaan PERTAMA (Fase 0 mencatat 2/4).

## HIJAU — diisi setelah deploy (lihat bagian bawah berkas ini)
