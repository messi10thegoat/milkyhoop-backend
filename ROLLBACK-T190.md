# ROLLBACK T190 — PIPELINE ENTITAS V2 (create_quote, di balik flag)

Cabang: `feat/t190-pipa-entitas-v2`, worktree `/root/mh-t190`.
Basis: master `3753b9f433a6d0accb7838c9f6e107e2e6822a2b`.

## Keadaan penyebaran
TIDAK ADA. Cabang ini TIDAK di-merge, TIDAK di-deploy, kontainer TIDAK
di-restart. Produksi `/root/milkyhoop-dev` tetap di master 3753b9f4.
Flag `PIPELINE_ENTITAS_V2` default OFF; walau kode ini sampai ke produksi
tanpa flag, jalur create_quote tidak berubah satu byte pun.

## Commit di cabang ini (urut; diisi saat dibuat)
- 7561bc6a docs(t190): berkas rollback SEBELUM menyentuh kode
- 35e344a3 feat(t190): Fase A/B/C (hasil_resolve, resolver_entitas, gerbang_keputusan)
- 48587dbf feat(t190): Fase D sambungan di balik flag + 75 tes
- (commit ini) docs(t190): isi SHA

Baseline suite unit 154 lulus; sesudah batch 229 lulus (154 lama TETAP
hijau, diukur terpisah dengan --ignore berkas tes baru).

## Cara membatalkan
Karena nol penyebaran, membatalkan = TIDAK melakukan apa pun.
Bila cabang ini terlanjur ter-merge dan perlu dicabut:

    git -C /root/milkyhoop-dev reset --keep 3753b9f433a6d0accb7838c9f6e107e2e6822a2b
    docker restart milkyhoop-dev-api_gateway

JANGAN `compose up --force-recreate` (ia membangun ulang image dan
mengubah lebih banyak daripada yang dicabut).

Pembatalan yang lebih murah dan harus dicoba LEBIH DULU: kosongkan /
hapus env `PIPELINE_ENTITAS_V2` lalu `docker restart
milkyhoop-dev-api_gateway`. Itu mengembalikan create_quote ke enricher
lama tanpa menyentuh git sama sekali.

## Berkas yang disentuh
BARU  backend/api_gateway/app/services/unified_agent/hasil_resolve.py
BARU  backend/api_gateway/app/services/unified_agent/resolver_entitas.py
BARU  backend/api_gateway/app/services/unified_agent/gerbang_keputusan.py
BARU  backend/api_gateway/tests/unit/test_t190_pipa_entitas.py
UBAH  backend/api_gateway/app/services/unified_agent/tool_executor.py
      (satu cabang berpagar flag sebelum `_enrich_payload`)
