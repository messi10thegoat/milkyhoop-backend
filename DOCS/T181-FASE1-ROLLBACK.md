# T181 FASE 1 — Rencana Rollback (ditulis SEBELUM menyentuh kode)

Tanggal tulis: 2026-08-30
Baseline master: a017914755d4fd252b195513d3ba1a6da81b3aaa
Branch: feat/t181-tolak (worktree /root/mh-t181c)
StartedAt produksi saat baseline: 2026-08-30T13:35:21Z

## Apa yang diubah
Empat situs `_enrich_*` di
`backend/api_gateway/app/services/unified_agent/tool_executor.py`
(`_enrich_sales_invoice`, `_enrich_sales_order`, `_enrich_purchase_invoice`,
`_enrich_quote`): kegagalan `json.loads(items)` TIDAK LAGI ditelan lalu
dikarang ulang dari slot skalar Stage-1. Sentinel `_t181_items_mentah`
disetel; jalur karangan digerbangi supaya kasus (A) (`items` tak pernah ada)
TETAP bekerja apa adanya.
Penanda `[T181_TOLAK]` terbit dari SATU situs: dispatcher `_enrich_payload`
(pelajaran T178). Isi string TIDAK dicetak ke log — hanya action + panjang.
Konsumsi sentinel: `_execute_propose_direct` (jalur hidup), `_execute_propose`,
dan jalur dry-run.

## Gejala yang menuntut ROLLBACK SEGERA (jangan mendiagnosis di produksi)
- Kontrol sehat mana pun tidak lagi menghasilkan kartu — terutama
  **bill SATU barang** (kasus A, jalur karangan yang SAH).
- Kartu penawaran/pesanan/faktur normal berubah jadi pesan penolakan.
- `[T181_TOLAK]` terbit dobel untuk satu kejadian.

## Perintah rollback (dijalankan owner di main tree)
    git -C /root/milkyhoop-dev status --short | head
    git -C /root/milkyhoop-dev reset --keep a0179147
    git -C /root/milkyhoop-dev rev-parse master   # HARUS a0179147...
    docker compose -f /root/milkyhoop-dev/docker-compose.yml up -d api_gateway
    docker inspect -f "{{.State.StartedAt}}" milkyhoop-dev-api_gateway

JANGAN `git reset --hard` (tree kotor 75 entri frontend + 2 .env.bak milik
orang lain). `--keep` menjaga perubahan tak-ter-commit itu.

## Bukti deploy yang diterima
HANYA: pergeseran `StartedAt` + perbedaan perilaku lewat milkyhoop.com.
BUKAN md5 (bind-mount), BUKAN `inspect.getsource` via `docker exec python -c`.
