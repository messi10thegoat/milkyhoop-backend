"""
ETag middleware — RFC 7232 conditional GET support.

Computes a weak ETag (W/"<md5hex>") over the response body for successful
JSON GET responses, then honors `If-None-Match` by returning 304 Not Modified
with an empty body while preserving Cache-Control, ETag, and Vary headers.

Designed to pair with CacheControlMiddleware's `private, no-cache,
must-revalidate` policy: the browser MUST revalidate every request, but the
server can short-circuit to 304 (empty body) when nothing changed.

Constraints:
- GET only. Other methods pass through untouched.
- 2xx JSON only. Other status codes / content types pass through untouched.
- StreamingResponse and text/event-stream pass through untouched.
- Body size cap 1 MiB. Larger responses pass through untouched (memory safety).
- Idempotent: if downstream already set an ETag header, we do not recompute
  or override (per-route ETag emitters like chat_history.py win).

Middleware ordering: must be registered OUTER of CacheControlMiddleware so
that on the response path CacheControl runs first (setting Cache-Control),
then this middleware sees the populated Cache-Control and preserves it on
the 304.

Added 2026-05-28 alongside relaxation of TRANSACTION_PATTERNS bucket from
`no-store` to `private, no-cache, must-revalidate`.
"""

from __future__ import annotations

import hashlib

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response, StreamingResponse
from starlette.types import ASGIApp


_MAX_BODY_BYTES = 1_048_576  # 1 MiB


class ETagMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: ASGIApp):
        super().__init__(app)

    async def dispatch(self, request: Request, call_next):
        # GET only — all other methods bypass entirely.
        if request.method != "GET":
            return await call_next(request)

        response = await call_next(request)

        # Skip streaming responses outright.
        if isinstance(response, StreamingResponse):
            return response

        # Status code gate: 2xx only.
        if not (200 <= response.status_code < 300):
            return response

        # Content-Type gate: application/json (allow charset suffix).
        content_type = response.headers.get("content-type", "").lower()
        if not content_type.startswith("application/json"):
            return response

        # SSE safety net.
        if "text/event-stream" in content_type:
            return response

        # Idempotency: downstream already emitted ETag, do not touch.
        if response.headers.get("etag"):
            return response

        # Drain body.
        body_chunks: list[bytes] = []
        total = 0
        oversize = False
        async for chunk in response.body_iterator:
            if not isinstance(chunk, (bytes, bytearray)):
                chunk = str(chunk).encode("utf-8")
            body_chunks.append(bytes(chunk))
            total += len(chunk)
            if total > _MAX_BODY_BYTES:
                oversize = True
                break

        if oversize:
            # Drain remainder, reconstruct without ETag.
            async for extra in response.body_iterator:
                if not isinstance(extra, (bytes, bytearray)):
                    extra = str(extra).encode("utf-8")
                body_chunks.append(bytes(extra))
            body = b"".join(body_chunks)
            headers = dict(response.headers)
            headers.pop("content-length", None)
            return Response(
                content=body,
                status_code=response.status_code,
                headers=headers,
                media_type=response.media_type,
            )

        body = b"".join(body_chunks)
        etag = 'W/"' + hashlib.md5(body).hexdigest() + '"'

        # 304 short-circuit DISABLED 2026-06-04 (hotfix): prod FE bundle
        # (build 2026-04-03) treats !res.ok as throw, so 304 triggered
        # infinite refetch loop. ETag header still emitted for browser-side
        # cache reuse; server just always sends full body.
        # inm = request.headers.get("if-none-match")
        # if inm and (inm == etag or inm.strip() == etag):
        #     ...return 304...

        headers = dict(response.headers)
        headers.pop("content-length", None)
        headers["ETag"] = etag
        return Response(
            content=body,
            status_code=response.status_code,
            headers=headers,
            media_type=response.media_type,
        )
