#!/usr/bin/env python3
"""
smoke_test.py — Phase 2A Step 4

Embed a query string and return top-K userguide_chunks by cosine similarity.
No permission filter (owner-equivalent). For sanity check only.

Usage (in api_gateway container):
  DATABASE_URL=postgresql://postgres:Proyek771977@postgres:5432/milkydb \
    python3 smoke_test.py "Cara bikin faktur penjualan baru"

Or pass multiple queries via stdin (one per line):
  cat queries.txt | python3 smoke_test.py --stdin
"""
from __future__ import annotations

import asyncio
import json
import os
import sys

import asyncpg
from openai import OpenAI

EMBED_MODEL = "text-embedding-3-small"
TOP_K = 5


def vector_literal(vec):
    return "[" + ",".join(f"{x:.7f}" for x in vec) + "]"


async def search(conn, client, query: str) -> dict:
    resp = client.embeddings.create(model=EMBED_MODEL, input=[query])
    emb = resp.data[0].embedding
    rows = await conn.fetch(
        """
        SELECT doc_id, doc_title, section_heading, tier, module, type,
               1 - (embedding <=> $1::vector) AS similarity
          FROM userguide_chunks
         ORDER BY embedding <=> $1::vector
         LIMIT $2
        """,
        vector_literal(emb), TOP_K,
    )
    results = [dict(r) for r in rows]
    for r in results:
        r["similarity"] = float(r["similarity"])
    return {
        "query": query,
        "top_similarity": results[0]["similarity"] if results else 0.0,
        "top_doc_id": results[0]["doc_id"] if results else None,
        "results": results,
    }


async def main(queries: list[str]) -> int:
    api_key = os.environ["OPENAI_API_KEY"]
    db_url = os.environ["DATABASE_URL"]
    client = OpenAI(api_key=api_key)
    conn = await asyncpg.connect(db_url)
    try:
        for q in queries:
            r = await search(conn, client, q)
            print(json.dumps({
                "query": r["query"],
                "top_similarity": round(r["top_similarity"], 4),
                "top_doc_id": r["top_doc_id"],
                "top5": [
                    {"doc_id": x["doc_id"],
                     "section": x["section_heading"],
                     "sim": round(x["similarity"], 4)}
                    for x in r["results"]
                ],
            }, ensure_ascii=False, indent=2))
            print()
    finally:
        await conn.close()
    return 0


if __name__ == "__main__":
    args = sys.argv[1:]
    if "--stdin" in args:
        qs = [ln.strip() for ln in sys.stdin if ln.strip()]
    elif args:
        qs = [" ".join(args)]
    else:
        print("usage: smoke_test.py 'query'  |  --stdin", file=sys.stderr)
        sys.exit(2)
    sys.exit(asyncio.run(main(qs)))
