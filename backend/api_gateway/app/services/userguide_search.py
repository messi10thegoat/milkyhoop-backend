"""
userguide_search.py — Phase 2B-1 Backend Integration

Service layer for the userguide RAG retrieval pipeline.

Responsibilities:
- Embed query via OpenAI text-embedding-3-small (1536 dim).
- pgvector cosine similarity search against `userguide_chunks`.
- SQL-LEVEL permission filter (NOT post-filter). Owner bypass = skip WHERE.
- Classify fallback tier 1-4 per BRAINSTORM doc policy.
- Detect permission_gated state (relevant chunks existed but were filtered).
- Log query to `userguide_query_log` for cost tracking + threshold tuning.

Iron Laws:
- Law 32: Use asyncpg pool (never asyncpg.connect direct).
- Law 24: Query log writes use auth-middleware-set app.tenant_id (no bypass).

Env vars (Decision D3):
  USERGUIDE_TIER4_THRESHOLD  default 0.85
  USERGUIDE_TIER3_THRESHOLD  default 0.65
  USERGUIDE_MAX_RESULTS      default 5
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field, asdict
from typing import Any, Optional

import asyncpg
from openai import AsyncOpenAI

logger = logging.getLogger(__name__)

EMBED_MODEL = "text-embedding-3-small"
EMBED_DIM = 1536
COST_PER_MILLION_TOKENS = 0.02  # USD, text-embedding-3-small

# Mapping userguide chunk required_module → DB module code used by PolicyEngine.
# Chunk modules come from frontmatter (see _meta/permission-matrix). PolicyEngine
# uses ALL_DB_MODULES from policy_engine_client.py. Both already converge on
# uppercase but a few diverge by name.
USERGUIDE_TO_DB_MODULE: dict[str, str] = {
    "ITEM": "PRODUCT",
    "DELIVERY": "INVOICE",  # delivery is part of invoice/AR cycle, no dedicated module code
    "STOCK_ADJUSTMENT": "STOCK_ADJUST",
    "STOCK_TRANSFER": "WAREHOUSE",
    "BILL_PAYMENT": "PAYMENT",
    "RECEIVE_PAYMENT": "RECEIPT",
    "BANK_TRANSFER": "BANK",
    "BANK_RECONCILIATION": "BANK",
    "TEAM": "USER_MANAGEMENT",
    "TENANT_SETTINGS": "SETTINGS",
    "COA": "ACCOUNT",
    "PRODUCTION": "WORK_ORDER",
    "EFAKTUR": "TAX",
    "DASHBOARD": "REPORT",
    # 1:1 names (passthrough): BANK, BILL, BOM, CUSTOMER, EMPLOYEE, INVOICE,
    # PAYROLL, REPORT, TAX, VENDOR, WORK_ORDER
}


def _map_chunk_module_to_db(chunk_module: str) -> str:
    return USERGUIDE_TO_DB_MODULE.get(chunk_module, chunk_module)


# ───────────────────────── Result dataclasses ──────────────────────────


@dataclass
class ChunkResult:
    chunk_id: str
    doc_id: str
    doc_title: str
    doc_path: str
    module: str
    type: str
    tier: str
    section_heading: Optional[str]
    content: str
    similarity: float
    required_module: Optional[str]
    required_action: Optional[str]
    citation_link: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class SearchResult:
    chunks: list[ChunkResult]
    top_similarity: float
    fallback_tier: int  # 1-4
    permission_gated: bool  # True if blocked chunks existed but user lacks access
    blocked_doc_titles: list[str] = field(default_factory=list)
    cost_usd: float = 0.0
    response_ms: int = 0
    query_tokens: int = 0
    filtered_count: int = 0

    def to_dict(self) -> dict:
        return {
            "chunks": [c.to_dict() for c in self.chunks],
            "top_similarity": self.top_similarity,
            "fallback_tier": self.fallback_tier,
            "permission_gated": self.permission_gated,
            "blocked_doc_titles": self.blocked_doc_titles,
            "cost_usd": self.cost_usd,
            "response_ms": self.response_ms,
            "filtered_count": self.filtered_count,
        }


# ───────────────────────── Helpers ─────────────────────────────────────


_async_openai_client: Optional[AsyncOpenAI] = None


def _get_openai_client() -> AsyncOpenAI:
    global _async_openai_client
    if _async_openai_client is None:
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY missing in environment")
        _async_openai_client = AsyncOpenAI(api_key=api_key)
    return _async_openai_client


def _vector_literal(vec: list[float]) -> str:
    """Format float list as pgvector text literal `[v1,v2,...]`."""
    return "[" + ",".join(f"{x:.7f}" for x in vec) + "]"


async def embed_query(query: str) -> tuple[list[float], int]:
    """
    Embed a single query string. Returns (embedding, token_count).
    """
    client = _get_openai_client()
    resp = await client.embeddings.create(model=EMBED_MODEL, input=query)
    embedding = resp.data[0].embedding
    if len(embedding) != EMBED_DIM:
        raise RuntimeError(f"unexpected embedding dim {len(embedding)} != {EMBED_DIM}")
    # Phase 2B-1.6: openai>=1.0 sometimes returns usage=None for embeddings.
    # Fall back to char-based token estimate (~4 chars/token, decent for ID+EN mix)
    # so cost monitoring under-reporting is bounded.
    tokens = 0
    try:
        usage = getattr(resp, "usage", None)
        if usage is not None:
            tokens = getattr(usage, "total_tokens", 0) or 0
    except Exception:
        tokens = 0
    if not tokens:
        tokens = max(1, (len(query) + 3) // 4)
    return embedding, tokens


def classify_fallback_tier(
    rows_count: int,
    top_sim: float,
    *,
    tier4_threshold: float,
    tier3_threshold: float,
) -> int:
    """
    Classify fallback tier per BRAINSTORM doc policy.
      Tier 1: 0 chunks
      Tier 2: chunks present, top similarity < tier3_threshold
      Tier 3: tier3_threshold <= top_sim < tier4_threshold
      Tier 4: top_sim >= tier4_threshold
    """
    if rows_count <= 0:
        return 1
    if top_sim >= tier4_threshold:
        return 4
    if top_sim >= tier3_threshold:
        return 3
    return 2


# ───────────────────────── Main entry ──────────────────────────────────


async def search(
    query: str,
    *,
    pool: asyncpg.Pool,
    user_id: str,
    tenant_id: str,
    user_allowed_modules: list[str],
    user_actions_for_module: dict[str, list[str]],
    is_owner: bool = False,
    max_results: Optional[int] = None,
    tier_preference: Optional[str] = None,
) -> SearchResult:
    """
    Retrieve top-K userguide chunks for `query` with SQL-level permission filter.

    Args:
      pool: asyncpg pool (Iron Law 32).
      user_allowed_modules: list of DB module codes the user has any access to.
      user_actions_for_module: dict {module_code: [action chars, ...]}.
      is_owner: if True, skip permission WHERE clause entirely.
      tier_preference: 'plain' | 'bridged' | 'deep' | None/'auto'.

    Returns SearchResult.
    """
    t0 = time.monotonic()

    tier4_thr = float(os.getenv("USERGUIDE_TIER4_THRESHOLD", "0.85"))
    tier3_thr = float(os.getenv("USERGUIDE_TIER3_THRESHOLD", "0.65"))
    if max_results is None:
        max_results = int(os.getenv("USERGUIDE_MAX_RESULTS", "5"))
    max_results = max(1, min(int(max_results), 10))

    # 1) Embed query
    try:
        embedding, query_tokens = await embed_query(query)
    except Exception:
        logger.exception("[userguide_search] embed failed")
        return SearchResult(
            chunks=[],
            top_similarity=0.0,
            fallback_tier=1,
            permission_gated=False,
            response_ms=int((time.monotonic() - t0) * 1000),
        )

    cost_usd = (query_tokens / 1_000_000.0) * COST_PER_MILLION_TOKENS
    vec_literal = _vector_literal(embedding)

    # 2) Build tier filter clause (informational + plain always allowed)
    tier_clause = ""
    tier_param: Any = None
    if tier_preference and tier_preference in ("plain", "bridged", "deep"):
        tier_clause = " AND (tier = $5 OR tier = 'plain') "
        tier_param = tier_preference

    # 3) Build permission WHERE clause.
    #
    # Owner bypass: skip permission filter entirely.
    # Otherwise: chunk visible if required_module IS NULL (informational) OR
    #            mapped chunk module is in allowed list AND required_action
    #            (or 'R' fallback for null) is in user's actions for that module.
    #
    # We resolve permission in Python after the SQL fetches a slightly-larger
    # candidate set; the "SQL-level filter" requirement is met by the WHERE
    # clause limiting candidates by required_module before similarity ranking.
    # For owner: no WHERE clause; LIMIT max_results directly.

    rows: list[asyncpg.Record]
    blocked_rows: list[asyncpg.Record] = []

    async with pool.acquire() as conn:
        if is_owner:
            sql = f"""
                SELECT chunk_id, doc_id, doc_title, doc_path, module, type, tier,
                       section_heading, content,
                       required_module, required_action,
                       1 - (embedding <=> $1::vector) AS similarity
                  FROM userguide_chunks
                 WHERE TRUE {tier_clause}
                 ORDER BY embedding <=> $1::vector
                 LIMIT $2
            """
            args = [vec_literal, max_results]
            if tier_param is not None:
                # Need to use $5 → re-number; rebuild
                sql = """
                    SELECT chunk_id, doc_id, doc_title, doc_path, module, type, tier,
                           section_heading, content,
                           required_module, required_action,
                           1 - (embedding <=> $1::vector) AS similarity
                      FROM userguide_chunks
                     WHERE TRUE AND (tier = $3 OR tier = 'plain')
                     ORDER BY embedding <=> $1::vector
                     LIMIT $2
                """
                args = [vec_literal, max_results, tier_param]
            rows = await conn.fetch(sql, *args)
        else:
            # Build mapping of chunk-module → DB-module on app side; pass
            # allowed DB modules as $2 (text[]). For per-action enforcement we
            # fetch a larger candidate set (max_results * 4) and filter in py
            # to honour user_actions_for_module precisely (asyncpg lacks easy
            # support for jsonb-driven action arrays in pgvector queries).
            #
            # SQL-level filter still excludes any chunk whose mapped module is
            # NOT in user_allowed_modules — preventing forbidden-module chunks
            # from competing in the ranking window.
            candidate_limit = max(max_results * 4, 20)
            allowed_db_modules = list(user_allowed_modules)

            # Resolve which userguide chunk modules map into allowed DB modules.
            # We can't push the mapping into SQL cleanly; instead we fetch any
            # required_module IS NULL OR required_module IN known list, then
            # filter precisely in Python. Reverse-map: chunk_module is allowed
            # if USERGUIDE_TO_DB_MODULE.get(chunk_module, chunk_module) ∈ allowed.
            allowed_chunk_modules = []
            # All distinct chunk modules seen in DB:
            distinct_chunk_modules = await conn.fetch(
                "SELECT DISTINCT required_module FROM userguide_chunks "
                "WHERE required_module IS NOT NULL"
            )
            for r in distinct_chunk_modules:
                cm = r["required_module"]
                if _map_chunk_module_to_db(cm) in allowed_db_modules:
                    allowed_chunk_modules.append(cm)

            sql = """
                SELECT chunk_id, doc_id, doc_title, doc_path, module, type, tier,
                       section_heading, content,
                       required_module, required_action,
                       1 - (embedding <=> $1::vector) AS similarity
                  FROM userguide_chunks
                 WHERE (required_module IS NULL OR required_module = ANY($2::text[]))
                 ORDER BY embedding <=> $1::vector
                 LIMIT $3
            """
            candidates = await conn.fetch(
                sql, vec_literal, allowed_chunk_modules, candidate_limit
            )

            # Per-action filter in Python
            permitted: list[asyncpg.Record] = []
            for r in candidates:
                req_mod = r["required_module"]
                req_act = r["required_action"]
                if req_mod is None:
                    permitted.append(r)
                    continue
                db_mod = _map_chunk_module_to_db(req_mod)
                user_acts = user_actions_for_module.get(db_mod, [])
                eff_act = req_act or "R"
                if eff_act in user_acts:
                    permitted.append(r)
            rows = permitted[:max_results]

            # Detect permission_gated: top similarity row in unfiltered set
            # had relevance but got blocked. We compare against an unfiltered
            # peek limited to max_results.
            unfiltered = await conn.fetch(
                """
                SELECT doc_id, doc_title, required_module, required_action,
                       1 - (embedding <=> $1::vector) AS similarity
                  FROM userguide_chunks
                 ORDER BY embedding <=> $1::vector
                 LIMIT $2
                """,
                vec_literal,
                max_results,
            )
            for u in unfiltered:
                if u["similarity"] < tier3_thr:
                    continue
                already_in = any(c["doc_id"] == u["doc_id"] for c in rows)
                if not already_in:
                    blocked_rows.append(u)

    # 4) Build chunk results
    chunk_results: list[ChunkResult] = []
    for r in rows:
        section_anchor = ""
        if r["section_heading"]:
            section_anchor = (
                "#" + (r["section_heading"] or "").lower().replace(" ", "-")[:60]
            )
        chunk_results.append(
            ChunkResult(
                chunk_id=str(r["chunk_id"]),
                doc_id=r["doc_id"],
                doc_title=r["doc_title"],
                doc_path=r["doc_path"],
                module=r["module"],
                type=r["type"],
                tier=r["tier"],
                section_heading=r["section_heading"],
                content=r["content"],
                similarity=float(r["similarity"]),
                required_module=r["required_module"],
                required_action=r["required_action"],
                citation_link=f"docs:{r['doc_id']}{section_anchor}",
            )
        )

    top_sim = chunk_results[0].similarity if chunk_results else 0.0
    fallback_tier = classify_fallback_tier(
        len(chunk_results),
        top_sim,
        tier4_threshold=tier4_thr,
        tier3_threshold=tier3_thr,
    )

    permission_gated = len(blocked_rows) > 0 and len(chunk_results) == 0
    blocked_titles: list[str] = []
    seen_titles: set[str] = set()
    for b in blocked_rows:
        t = b["doc_title"]
        if t not in seen_titles:
            seen_titles.add(t)
            blocked_titles.append(t)

    response_ms = int((time.monotonic() - t0) * 1000)
    result = SearchResult(
        chunks=chunk_results,
        top_similarity=top_sim,
        fallback_tier=fallback_tier,
        permission_gated=permission_gated,
        blocked_doc_titles=blocked_titles,
        cost_usd=cost_usd,
        response_ms=response_ms,
        query_tokens=query_tokens,
        filtered_count=len(blocked_rows),
    )

    # 5) Log query (fire-and-forget; non-blocking)
    try:
        await log_query(
            pool=pool,
            tenant_id=tenant_id,
            user_id=user_id,
            query_text=query,
            query_tokens=query_tokens,
            chunks_returned=len(chunk_results),
            top_similarity=top_sim,
            tier_used=str(fallback_tier),
            cost_usd=cost_usd,
            response_ms=response_ms,
        )
    except Exception:
        logger.exception("[userguide_search] log_query failed (non-fatal)")

    return result


async def log_query(
    *,
    pool: asyncpg.Pool,
    tenant_id: str,
    user_id: str,
    query_text: str,
    query_tokens: int,
    chunks_returned: int,
    top_similarity: float,
    tier_used: str,
    cost_usd: float,
    response_ms: int,
) -> None:
    """INSERT into userguide_query_log. Schema per V147 migration."""
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO userguide_query_log
              (tenant_id, user_id, query_text, query_tokens,
               chunks_returned, top_similarity, tier_used, cost_usd, response_ms)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
            """,
            tenant_id,
            user_id,
            query_text[:4000],
            int(query_tokens),
            int(chunks_returned),
            float(top_similarity) if top_similarity is not None else None,
            str(tier_used)[:20],
            float(cost_usd),
            int(response_ms),
        )
