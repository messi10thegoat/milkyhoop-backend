# ROLLBACK — T182-A: `items` sebagai ARRAY untuk create_sales_invoice

Branch: `feat/t182a-array-si`
Worktree: `/root/mh-t182a`
Basis: master `97d9cff633d01dceb5c7e764ccac8f64774a9e30`

## Status
BELUM di-merge. BELUM di-deploy. Produksi (`/root/milkyhoop-dev`) TIDAK disentuh.

## Commit di branch ini
(diperbarui tiap commit)
- `5d2f86e9` fix(chat): items create_sales_invoice dideklarasikan sebagai ARRAY (T182-A)
- `811cacb2` docs(rollback): catat commit T182-A + bukti gate
- `3a7137f4` docs(rollback): isi SHA commit dokumentasi
- `<terakhir>` docs(rollback): tutup daftar commit (commit dokumentasi TERAKHIR
  tak bisa menyebut SHA-nya sendiri — pastikan `git log --oneline master..HEAD`
  tidak menunjukkan commit di luar daftar ini)

## Berkas yang disentuh
- `backend/api_gateway/app/services/unified_agent/direct_action_registry.py`
  (mengisi `FieldSpec.item_schema` untuk `create_sales_invoice.items`)
- `backend/api_gateway/tests/unit/test_skema_items.py`
  (mengeluarkan `create_sales_invoice` dari `AKSI_TAK_DIUBAH`; menambah tes array SI)

TIDAK disentuh: `gemini_client.py` (`_clean_schema`), `entity_extractor.py`,
`tool_executor.py`, jalur enricher/scalar-fallback/gerbang entitas.

## Langkah darurat (kalau sudah terlanjur di-merge + deploy)
```
git -C /root/milkyhoop-dev reset --keep 97d9cff633d01dceb5c7e764ccac8f64774a9e30
docker restart milkyhoop-dev-api_gateway
```
JANGAN `docker compose up --force-recreate` (compose stale — lihat memory
redis-misconf-capdrop-20260725).

## Membuang branch ini seluruhnya
```
git -C /root/milkyhoop-dev worktree remove /root/mh-t182a
git -C /root/milkyhoop-dev branch -D feat/t182a-array-si
```

## Bukti (Gate A)
- Unit di branch: **83 passed** (baseline master = 81).
- Bukti MERAH di `97d9cff6` (tes dibawa, pemasangan TIDAK dibawa):
  **2 failed, 81 passed**, nol ModuleNotFoundError. Worktree bukti-merah
  `/root/mh-t182a-merah` sudah dihapus.
- Diff skema 61 aksi (dump `build_intent_schema` sebelum vs sesudah):
  tepat **1 aksi berubah** = `create_sales_invoice`. `create_bill`,
  `create_quote`, `create_sales_order`, `create_stock_adjustment`,
  `create_journal_entry`, dan 9 `update_*` byte-identik.

## Risiko sisa
Perubahan HANYA deklarasi skema yang dikirim ke model. Jalur hilir
(enricher, scalar-fallback) tetap utuh dan tetap jadi jaring bila model
sesekali kembali mengirim string.
