# T179-Q3 FASE 0 — Rencana rollback (ditulis SEBELUM kode disentuh)

Basis: master `a017914755d4fd252b195513d3ba1a6da81b3aaa`
Branch: `feat/t179q3-array-bill` di worktree `/root/mh-t179q3`
StartedAt produksi sebelum deploy: `2026-08-30T14:02:06Z`

## Berkas yang disentuh
- backend/api_gateway/app/services/unified_agent/direct_action_registry.py (FieldSpec.item_schema + isi untuk create_bill.items)
- backend/api_gateway/app/services/unified_agent/entity_extractor.py (build_intent_schema: cabang array)
- DOCS/rollback/T179Q3-ROLLBACK.md (berkas ini)
- backend/api_gateway/tests/chat/test_t179q3_array_schema.py (gate offline)

## Rollback — CARA YANG DIANJURKAN (satu langkah, tak bisa mandek)
git -C /root/milkyhoop-dev reset --keep a017914755d4fd252b195513d3ba1a6da81b3aaa
docker restart milkyhoop-dev-api_gateway
docker inspect -f '{{.State.StartedAt}}' milkyhoop-dev-api_gateway   # WAJIB bergeser

## Rollback — kalau harus lewat revert
Cabut SEMUA commit branch ini, urutan terbalik (isi SHA sesudah commit dibuat):
  git -C /root/milkyhoop-dev revert --no-edit 796dc4029d7074574a4538f21ba442f1c64ae5d5 1244b74c8acd93ec2cb48542803317dde1ca54b5
Satu `git revert` untuk satu SHA TIDAK CUKUP dan sudah pernah MANDEK di tiket
sebelumnya karena commit di atasnya menyentuh baris yang sama.

## Bukti rollback yang diterima
Pergeseran StartedAt + differential perilaku ([EXTRACT_S2] tipe kembali ke str).
BUKAN md5. BUKAN inspect.getsource lewat docker exec python -c.
