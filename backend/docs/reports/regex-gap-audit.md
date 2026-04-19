# Regex Gap Audit — classify_query_intent()

**Date**: 2026-04-11
**File**: `api_gateway/app/services/unified_agent/entity_extractor.py`

## Patched Gaps (4 new regex blocks, 6 patterns total)

| # | Pattern | Intent | Variations Covered |
|---|---------|--------|--------------------|
| 1 | `(?:ada\|apa)\s+(?:stok\|barang\|item\|produk)\s+(?:apa\|yang)` | query_items_search | "ada stok apa saja", "apa barang yang", "ada item apa" |
| 2 | `(?:stok\|barang\|item\|produk)\s+(?:apa\s+(?:saja\|aja)\|yang\s+(?:tersedia\|ada))` | query_items_search | "barang apa saja", "produk yang tersedia", "stok apa aja" |
| 3 | `(?:produk\|barang\|item)\s+apa\s+yang\s+(?:ada\|tersedia)` | query_items_search | "produk apa yang ada di gudang" |
| 4 | `(?:uang\|duit\|kas)(?:\s+\w+){0,3}\s*(?:berapa\|tinggal\|sisa\|masih)` | query_cash_balance | "uang berapa", "duit tinggal berapa", "kas masih berapa" |
| 5 | `(?:untung\|rugi\|profit)\s+(?:ga\|gak\|nggak\|tidak\|atau\|kita\|gimana\|bagaimana\|berapa)` | query_profit_loss | "untung ga", "profit kita gimana", "rugi atau untung" |
| 6 | `(?:bulan\s*ini\|minggu\s*ini)\s+(?:habis\|keluar\|spend)\s+(?:berapa\|banyak)` + `(?:pengeluaran\|biaya\|expense)\s+(?:bulan\|minggu\|hari)\s*(?:ini)\s*(?:berapa\|banyak\|total)` | query_expenses_summary | "bulan ini habis berapa", "pengeluaran bulan ini berapa" |

## Regex Classification Test (unit-level, 0ms)

All 6 patched patterns correctly classify to expected intents. 28/28 test queries classified correctly (including pre-existing patterns).

## E2E Latency Audit (25 queries)

**Note**: E2E latency includes Gemini intent extraction + tool call + GPT-4o-mini response. The regex classifier runs in 0ms but currently only serves as ARAP guard — it does NOT bypass the LLM pipeline. Latency is dominated by LLM round-trips.

| Query | Category | Latency | Path | Iterations |
|-------|----------|---------|------|------------|
| ada stok barang apa saja di sistem? | items | 23.8s | AGENT_LOOP | 2 |
| barang apa saja yang tersedia? | items | 17.8s | AGENT_LOOP | 2 |
| produk apa yang ada di gudang? | items | 19.3s | AGENT_LOOP | 2 |
| uang kita berapa sekarang? | cash | 8.1s | AGENT_LOOP | 2 |
| duit tinggal berapa? | cash | 8.9s | AGENT_LOOP | 2 |
| saldo kas berapa? | cash | 5.4s | SLOW_PIPELINE | 2 |
| untung ga bulan ini? | pnl | 9.1s | AGENT_LOOP | 2 |
| kita profit atau rugi? | pnl | 12.6s | AGENT_LOOP | 2 |
| laba rugi bulan ini gimana? | pnl | 6.5s | SLOW_PIPELINE | 2 |
| bulan ini habis berapa? | expense | 6.3s | SLOW_PIPELINE | 2 |
| pengeluaran bulan ini berapa? | expense | 17.0s | AGENT_LOOP | 2 |
| total piutang kita berapa? | ar | 21.5s | AGENT_LOOP | 2 |
| total hutang kita berapa? | ap | 9.7s | AGENT_LOOP | 2 |
| tagihan mana yang belum lunas? | ap | 16.4s | AGENT_LOOP | 2 |
| daftar pelanggan siapa saja? | customer | 6.5s | SLOW_PIPELINE | 2 |
| neraca saldo terbaru? | report | 10.7s | AGENT_LOOP | 2 |
| stok rendah apa saja? | items | 11.2s | AGENT_LOOP | 2 |
| barang yang stoknya habis? | items | 10.5s | AGENT_LOOP | 2 |
| berapa total penjualan bulan ini? | calc | 7.7s | SLOW_PIPELINE | 2 |
| ranking vendor berdasarkan hutang? | calc | 8.2s | AGENT_LOOP | 2 |
| daftar jurnal bulan ini? | journal | 49.6s | AGENT_LOOP | 2 |
| siapa yang belum bayar? | ar | 6.3s | SLOW_PIPELINE | 3 |
| vendor mana yang kita belum bayar? | ap | 7.1s | SLOW_PIPELINE | 3 |
| arus kas bulan ini? | report | 7.8s | SLOW_PIPELINE | 2 |
| faktur penjualan yang belum dibayar? | ar | 5.3s | SLOW_PIPELINE | 2 |

## Summary

- **PIPELINE (<3s)**: 0/25
- **SLOW_PIPELINE (3-8s)**: 9/25
- **AGENT_LOOP (>8s)**: 16/25

## Key Finding

The regex classifier works correctly at the classification level (0ms, deterministic). However, E2E latency is **not determined by regex classification** — it's determined by:

1. **Gemini intent extraction** (LLM call, ~2-5s) — runs AFTER `_infer_intent` heuristic, regardless of regex match
2. **Tool execution** (~0.1-0.5s)
3. **GPT-4o-mini response generation** (~3-20s depending on data volume)

### Implication for LLM Router Phase 2

To achieve <3s responses, the architecture needs to **bypass the Gemini extraction step entirely** when `classify_query_intent()` returns a high-confidence match. Current flow:

```
_infer_intent() → SIMPLE_READ → Gemini extraction → tool call → GPT-4o-mini response
```

Proposed Phase 2 flow:
```
classify_query_intent() → direct tool call → template response (no LLM)
```

## Remaining Unpatched Gaps (fall to LLM)

- "siapa yang belum bayar?" — no explicit piutang/hutang keyword, classified as NONE by regex (LLM handles OK, 6.3s)
- "vendor mana yang kita belum bayar?" — NONE by regex (LLM handles OK, 7.1s)

These are acceptable — the LLM handles them correctly in 6-7s.
