# ROLLBACK — T182-A: `items` sebagai ARRAY untuk create_sales_invoice

Branch: `feat/t182a-array-si`
Worktree: `/root/mh-t182a`
Basis: master `97d9cff633d01dceb5c7e764ccac8f64774a9e30`

## Status
BELUM di-merge. BELUM di-deploy. Produksi (`/root/milkyhoop-dev`) TIDAK disentuh.

## Commit di branch ini
(diperbarui tiap commit)
- (belum ada)

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

## Risiko sisa
Perubahan HANYA deklarasi skema yang dikirim ke model. Jalur hilir
(enricher, scalar-fallback) tetap utuh dan tetap jadi jaring bila model
sesekali kembali mengirim string.
