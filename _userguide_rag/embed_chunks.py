#!/usr/bin/env python3
"""
embed_chunks.py — Phase 2A Step 3

Reads _build/chunks.jsonl, calls OpenAI text-embedding-3-small in batches,
UPSERTs into userguide_chunks. Idempotent: skips chunks whose
(doc_id, chunk_index) already exists with the same content_hash.

Run on the SERVER (where DB + .env live):
  scp -> /root/milkyhoop-dev/_userguide_rag/embed_chunks.py
  scp -> .../chunks.jsonl
  ssh root@... 'cd /root/milkyhoop-dev && OPENAI_API_KEY=$(grep ^OPENAI_API_KEY .env | cut -d= -f2-) \
    DATABASE_URL=postgresql://postgres:Proyek771977@postgres:5432/milkydb \
    python3 _userguide_rag/embed_chunks.py _userguide_rag/chunks.jsonl'
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from datetime import date, datetime
from pathlib import Path
from typing import Any

import asyncpg
from openai import OpenAI

EMBED_MODEL = "text-embedding-3-small"
EMBED_DIM = 1536
BATCH_SIZE = 100
COST_PER_MILLION = 0.02  # USD per 1M tokens (text-embedding-3-small)


def load_chunks(path: Path) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            out.append(json.loads(line))
    return out


def _parse_date(s: str | date) -> date:
    if isinstance(s, date):
        return s
    s = str(s).strip()
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%d-%m-%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    return date(2026, 1, 1)


def vector_literal(vec: list[float]) -> str:
    return "[" + ",".join(f"{x:.7f}" for x in vec) + "]"


async def fetch_existing_hashes(conn: asyncpg.Connection) -> dict[tuple[str, int], str]:
    rows = await conn.fetch("SELECT doc_id, chunk_index, content_hash FROM userguide_chunks")
    return {(r["doc_id"], r["chunk_index"]): r["content_hash"] for r in rows}


async def upsert_batch(conn: asyncpg.Connection, items: list[tuple[dict, list[float]]]) -> None:
    sql = """
        INSERT INTO userguide_chunks (
            doc_id, doc_title, doc_path, module, type, tier,
            section_heading, section_level, chunk_index, content,
            content_tokens, content_hash, embedding,
            required_module, required_action, related_ids, last_updated
        ) VALUES (
            $1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13::vector,$14,$15,$16,$17
        )
        ON CONFLICT (doc_id, chunk_index) DO UPDATE SET
            doc_title       = EXCLUDED.doc_title,
            doc_path        = EXCLUDED.doc_path,
            module          = EXCLUDED.module,
            type            = EXCLUDED.type,
            tier            = EXCLUDED.tier,
            section_heading = EXCLUDED.section_heading,
            section_level   = EXCLUDED.section_level,
            content         = EXCLUDED.content,
            content_tokens  = EXCLUDED.content_tokens,
            content_hash    = EXCLUDED.content_hash,
            embedding       = EXCLUDED.embedding,
            required_module = EXCLUDED.required_module,
            required_action = EXCLUDED.required_action,
            related_ids     = EXCLUDED.related_ids,
            last_updated    = EXCLUDED.last_updated,
            indexed_at      = NOW()
    """
    async with conn.transaction():
        for c, emb in items:
            await conn.execute(
                sql,
                c["doc_id"], c["doc_title"], c["doc_path"], c["module"], c["type"], c["tier"],
                c.get("section_heading"), c.get("section_level"), c["chunk_index"], c["content"],
                c["content_tokens"], c["content_hash"], vector_literal(emb),
                c.get("required_module"), c.get("required_action"),
                c.get("related_ids") or [], _parse_date(c["last_updated"]),
            )


async def main(jsonl_path: str) -> int:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        print("ABORT: OPENAI_API_KEY not set", file=sys.stderr)
        return 2
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        print("ABORT: DATABASE_URL not set", file=sys.stderr)
        return 2

    chunks = load_chunks(Path(jsonl_path))
    print(f"Loaded {len(chunks)} chunks from {jsonl_path}")

    client = OpenAI(api_key=api_key)
    conn = await asyncpg.connect(db_url)
    try:
        existing = await fetch_existing_hashes(conn)
        print(f"DB already has {len(existing)} chunk rows")

        todo: list[dict] = []
        skipped = 0
        for c in chunks:
            key = (c["doc_id"], c["chunk_index"])
            if existing.get(key) == c["content_hash"]:
                skipped += 1
                continue
            todo.append(c)
        print(f"To embed: {len(todo)} (skipped unchanged: {skipped})")

        total_tokens = 0
        t0 = time.time()
        for i in range(0, len(todo), BATCH_SIZE):
            batch = todo[i:i + BATCH_SIZE]
            inputs = [c["content"] for c in batch]
            resp = client.embeddings.create(model=EMBED_MODEL, input=inputs)
            usage_tokens = getattr(resp.usage, "total_tokens", 0) if resp.usage else 0
            total_tokens += usage_tokens
            embeddings = [d.embedding for d in resp.data]
            await upsert_batch(conn, list(zip(batch, embeddings)))
            print(f"  batch {i // BATCH_SIZE + 1}: {len(batch)} chunks, "
                  f"{usage_tokens} tokens (cum {total_tokens})")

        elapsed = time.time() - t0
        cost = total_tokens / 1_000_000 * COST_PER_MILLION
        print("")
        print(f"Indexed         : {len(todo)} chunks")
        print(f"Skipped (cache) : {skipped}")
        print(f"Tokens used     : {total_tokens}")
        print(f"Cost            : ${cost:.6f} USD")
        print(f"Elapsed         : {elapsed:.1f}s")

        final_count = await conn.fetchval("SELECT count(*) FROM userguide_chunks")
        print(f"DB row count    : {final_count}")
    finally:
        await conn.close()
    return 0


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else "_build/chunks.jsonl"
    sys.exit(asyncio.run(main(path)))
