"""Rewrite Gemini-emitted .md citation links to docs: scheme.

Phase 2B-1.8 — citation post-processor fix.

When the TUTORIAL agent loop runs on Gemini (Phase 2B-1.7 latency win),
the model copies chunk content patterns and emits links like:
    [Cara Bikin Faktur](faktur-penjualan/how-to/bikin-faktur-baru.md)
instead of the required:
    [Cara Bikin Faktur](docs:faktur-penjualan.how-to.bikin-faktur-baru)

The Phase 2B-2 frontend drawer ONLY listens for the `docs:` URI scheme.
This module rewrites those links deterministically using doc metadata
from the search_userguide tool result, preserving Gemini's latency win
without prompt-engineering retries.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Iterable

logger = logging.getLogger(__name__)

# Markdown link pattern: [text](url)
_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")

# Dotted doc_id pattern: e.g. faktur-penjualan.how-to.bikin-faktur-baru
_DOTTED_RE = re.compile(r"^[a-z0-9_-]+(\.[a-z0-9_-]+){2,}$")


def _normalize_path(path: str) -> str:
    """Strip leading ./ or / and an `id/` locale prefix."""
    p = path.lstrip("./").lstrip("/")
    if p.startswith("id/"):
        p = p[3:]
    return p


def rewrite_citations(text: str, chunks: Iterable[dict[str, Any]]) -> str:
    """Rewrite .md / path-style links to docs:doc_id scheme.

    Args:
        text: assistant response markdown
        chunks: iterable of dicts from search_userguide result, each with
                'doc_id' (e.g., 'faktur-penjualan.how-to.bikin-faktur-baru'),
                'doc_title', 'doc_path' (e.g., 'id/faktur-penjualan/how-to/bikin-faktur-baru.md')

    Returns:
        text with .md / path-style links rewritten to docs:doc_id when the
        target can be resolved against the chunk metadata. Untouched links
        are left as-is. Idempotent: existing docs:* links pass through.
    """
    if not text:
        return text

    chunks_list = list(chunks) if chunks is not None else []
    if not chunks_list:
        return text

    # Lookup tables
    by_path: dict[str, str] = {}
    by_basename: dict[str, str] = {}
    by_title: dict[str, str] = {}
    valid_doc_ids: set[str] = set()

    for c in chunks_list:
        if not isinstance(c, dict):
            continue
        doc_id = c.get("doc_id")
        if not doc_id:
            continue
        valid_doc_ids.add(doc_id)

        path = c.get("doc_path") or ""
        if path:
            normalized = _normalize_path(path)
            by_path.setdefault(normalized, doc_id)
            # Also key by file basename for partial path matches
            basename = normalized.rsplit("/", 1)[-1]
            by_basename.setdefault(basename, doc_id)

        title = c.get("doc_title") or ""
        if title:
            by_title.setdefault(title.lower().strip(), doc_id)

    def replace(match: re.Match[str]) -> str:
        text_part = match.group(1)
        url = match.group(2).strip()

        # docs: scheme — validate against retrieved chunks. Gemini hallucinates
        # plausible-looking but wrong doc_ids (e.g. 'faktur-penjualan.how-to.bikin-faktur-penjualan-baru'
        # when the real one is 'faktur-penjualan.how-to.bikin-faktur-baru' — model copies
        # sibling-doc naming convention). Drawer fetch then 404s.
        if url.startswith("docs:"):
            declared_id = url[5:].strip()
            if declared_id in valid_doc_ids:
                return match.group(0)
            # Hallucinated id — try to recover by matching anchor text to a real title
            fallback = by_title.get(text_part.lower().strip())
            if not fallback and chunks_list:
                # Last resort: top-similarity chunk (already on retrieval shortlist)
                fallback = chunks_list[0].get("doc_id")
            if fallback:
                return f"[{text_part}](docs:{fallback})"
            # No recoverable id — strip the link, keep anchor text plain
            return text_part

        # External URLs: leave alone
        if url.startswith(("http://", "https://", "mailto:", "tel:")):
            return match.group(0)

        # Strip query/fragment
        clean_url = url.split("#", 1)[0].split("?", 1)[0]
        normalized = _normalize_path(clean_url)

        # Try direct path match
        doc_id = by_path.get(normalized)

        # Try basename match
        if not doc_id:
            basename = normalized.rsplit("/", 1)[-1]
            doc_id = by_basename.get(basename)

        # Try anchor-text title match (case-insensitive)
        if not doc_id:
            doc_id = by_title.get(text_part.lower().strip())

        # URL itself looks like a dotted doc_id and matches retrieved set
        if not doc_id and _DOTTED_RE.match(clean_url) and clean_url in valid_doc_ids:
            doc_id = clean_url

        if doc_id:
            return f"[{text_part}](docs:{doc_id})"

        # No match — leave untouched (safer than guessing)
        return match.group(0)

    try:
        return _LINK_RE.sub(replace, text)
    except Exception:  # defensive: never break the response
        logger.exception("[citation_rewriter] rewrite failed; returning text unchanged")
        return text


def extract_userguide_chunks(
    tool_calls_log: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Pull chunks from all search_userguide entries in tool_calls_log.

    Tool-result shape (from userguide_search.search): payload['chunks'] is a
    list of dicts each carrying doc_id / doc_title / doc_path among others.
    """
    out: list[dict[str, Any]] = []
    for tc in tool_calls_log or []:
        if not isinstance(tc, dict):
            continue
        if tc.get("name") != "search_userguide":
            continue
        if not tc.get("success"):
            continue
        data = tc.get("data") or {}
        # data may be the raw search result dict, or wrapped
        chunks = None
        if isinstance(data, dict):
            chunks = data.get("chunks")
            if chunks is None and isinstance(data.get("data"), dict):
                chunks = data["data"].get("chunks")
        if isinstance(chunks, list):
            out.extend(c for c in chunks if isinstance(c, dict))
    return out


def ensure_citation_footer(
    text: str,
    chunks: list[dict],
    top_similarity: float,
    *,
    min_similarity: float = 0.65,
) -> str:
    """Append a citation footer if the LLM produced no docs: link.

    Phase 2B-2.0 deterministic fallback: Gemini 2.5 Flash sometimes ignores
    the citation_required directive for long step-by-step "cara bikin X"
    enumerations even when the search_userguide tool result contains
    pre-rendered [Title](docs:doc_id) examples (Phase 2B-1.9). This appends
    a single trailing reference using the top retrieved chunk so the
    frontend drawer always has something to attach to.

    Triggers only when:
    - chunks list non-empty
    - top_similarity >= min_similarity (skip very weak retrieval)
    - text does NOT already contain a `docs:` markdown link
    - text is not empty
    - top chunk has both doc_id and doc_title

    Footer format: ``\\n\\n_Referensi: [Title](docs:doc_id)_``
    """
    if not text or not chunks or top_similarity < min_similarity:
        return text
    # Already has at least one docs: link → no-op
    if re.search(r"\]\(docs:[a-zA-Z0-9._-]+\)", text):
        return text
    top = chunks[0]
    if not isinstance(top, dict):
        return text
    doc_id = top.get("doc_id")
    title = top.get("doc_title")
    if not doc_id or not title:
        return text
    sep = "\n\n" if not text.endswith("\n") else "\n"
    return f"{text.rstrip()}{sep}_Referensi: [{title}](docs:{doc_id})_"


# ─────────────────────────────────────────────────────────────────────
# Inline self-test (run via `python -m ...citation_rewriter`)
# ─────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    sample_chunks = [
        {
            "doc_id": "faktur-penjualan.how-to.bikin-faktur-baru",
            "doc_title": "Cara Bikin Faktur Penjualan Baru",
            "doc_path": "id/faktur-penjualan/how-to/bikin-faktur-baru.md",
        },
        {
            "doc_id": "terima-pembayaran.how-to.terima-pembayaran",
            "doc_title": "Cara Terima Pembayaran",
            "doc_path": "id/terima-pembayaran/how-to/terima-pembayaran.md",
        },
    ]

    cases = [
        # 1. Already correct passes through
        (
            "See [X](docs:faktur-penjualan.how-to.bikin-faktur-baru) for more.",
            "See [X](docs:faktur-penjualan.how-to.bikin-faktur-baru) for more.",
        ),
        # 2. .md path rewrites
        (
            "Lihat [Cara Bikin](faktur-penjualan/how-to/bikin-faktur-baru.md).",
            "Lihat [Cara Bikin](docs:faktur-penjualan.how-to.bikin-faktur-baru).",
        ),
        # 3. Basename only
        (
            "[Terima Pembayaran](terima-pembayaran.md)",
            "[Terima Pembayaran](docs:terima-pembayaran.how-to.terima-pembayaran)",
        ),
        # 4. https left alone
        (
            "[ext](https://example.com/foo.md)",
            "[ext](https://example.com/foo.md)",
        ),
        # 5. Unknown md left untouched
        (
            "[Unknown](does/not/exist.md)",
            "[Unknown](does/not/exist.md)",
        ),
        # 6. Title-based fallback (no path match)
        (
            "[Cara Bikin Faktur Penjualan Baru](something-weird.md)",
            "[Cara Bikin Faktur Penjualan Baru](docs:faktur-penjualan.how-to.bikin-faktur-baru)",
        ),
        # 7. Empty chunks: no-op
        # (handled separately)
    ]

    failures = 0
    for i, (inp, expected) in enumerate(cases, 1):
        got = rewrite_citations(inp, sample_chunks)
        ok = got == expected
        marker = "OK " if ok else "FAIL"
        print(f"[{marker}] case {i}: {got!r}")
        if not ok:
            print(f"    expected: {expected!r}")
            failures += 1

    # Empty chunks
    got = rewrite_citations("[X](path.md)", [])
    if got == "[X](path.md)":
        print("[OK ] case 7: empty chunks no-op")
    else:
        print(f"[FAIL] case 7: empty chunks no-op -> {got!r}")
        failures += 1

    # Empty text
    got = rewrite_citations("", sample_chunks)
    if got == "":
        print("[OK ] case 8: empty text no-op")
    else:
        print(f"[FAIL] case 8: empty text -> {got!r}")
        failures += 1

    # ── ensure_citation_footer self-tests ────────────────────────────
    footer_chunk = [
        {
            "doc_id": "faktur-pembelian.how-to.bikin-faktur-pembelian-baru",
            "doc_title": "Cara Bikin Faktur Pembelian Baru",
            "doc_path": "id/faktur-pembelian/how-to/bikin-faktur-pembelian-baru.md",
        }
    ]

    # F1: no chunks → no-op
    got = ensure_citation_footer("Hello world.", [], 0.9)
    if got == "Hello world.":
        print("[OK ] footer F1: no chunks no-op")
    else:
        print(f"[FAIL] footer F1 -> {got!r}")
        failures += 1

    # F2: low similarity (0.5 < 0.65) → no-op
    got = ensure_citation_footer("Step 1. Buka menu.", footer_chunk, 0.5)
    if got == "Step 1. Buka menu.":
        print("[OK ] footer F2: low similarity no-op")
    else:
        print(f"[FAIL] footer F2 -> {got!r}")
        failures += 1

    # F3: already has docs: link → no-op
    existing = (
        "Lihat [doc](docs:faktur-pembelian.how-to.bikin-faktur-pembelian-baru) ya."
    )
    got = ensure_citation_footer(existing, footer_chunk, 0.9)
    if got == existing:
        print("[OK ] footer F3: existing docs link no-op")
    else:
        print(f"[FAIL] footer F3 -> {got!r}")
        failures += 1

    # F4: empty text → no-op
    got = ensure_citation_footer("", footer_chunk, 0.9)
    if got == "":
        print("[OK ] footer F4: empty text no-op")
    else:
        print(f"[FAIL] footer F4 -> {got!r}")
        failures += 1

    # F5: all conditions met → footer appended
    body = "1. Buka menu.\n2. Tambah faktur.\n3. Simpan."
    got = ensure_citation_footer(body, footer_chunk, 0.79)
    expected_suffix = "_Referensi: [Cara Bikin Faktur Pembelian Baru](docs:faktur-pembelian.how-to.bikin-faktur-pembelian-baru)_"
    if got.endswith(expected_suffix) and "docs:faktur-pembelian" in got:
        print("[OK ] footer F5: footer appended")
    else:
        print(f"[FAIL] footer F5 -> {got!r}")
        failures += 1

    # F6: missing doc_id/title → no-op
    got = ensure_citation_footer("hello", [{"doc_id": None, "doc_title": "x"}], 0.9)
    if got == "hello":
        print("[OK ] footer F6: missing doc_id no-op")
    else:
        print(f"[FAIL] footer F6 -> {got!r}")
        failures += 1

    if failures:
        print(f"\n{failures} failure(s)")
        raise SystemExit(1)
    print("\nAll citation_rewriter self-tests passed.")
