"""
Request ID Middleware
Adds unique request ID for tracing and audit logging
"""
import uuid
import time
import logging
from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware

logger = logging.getLogger(__name__)


class RequestIDMiddleware(BaseHTTPMiddleware):
    """
    Adds a unique request ID to every request for:
    - Request tracing across services
    - Audit logging
    - Error correlation
    - Performance monitoring
    """

    HEADER_NAME = "X-Request-ID"

    async def dispatch(self, request: Request, call_next):
        # Get or generate request ID
        request_id = request.headers.get(self.HEADER_NAME)
        if not request_id:
            request_id = str(uuid.uuid4())

        # Store in request state for access in route handlers
        request.state.request_id = request_id
        request.state.request_start_time = time.time()

        # Add to logging context
        # Log request start
        client_ip = self._get_client_ip(request)
        logger.info(
            f"[{request_id[:8]}] {request.method} {request.url.path} "
            f"from {client_ip}",
            extra={
                "request_id": request_id,
                "method": request.method,
                "path": request.url.path,
                "client_ip": client_ip,
            }
        )

        # Process request
        response = await call_next(request)

        # Calculate duration
        duration_ms = (time.time() - request.state.request_start_time) * 1000

        # Add request ID to response headers
        response.headers[self.HEADER_NAME] = request_id
        response.headers["X-Response-Time"] = f"{duration_ms:.2f}ms"

        # Log request completion
        logger.info(
            f"[{request_id[:8]}] {response.status_code} in {duration_ms:.2f}ms",
            extra={
                "request_id": request_id,
                "status_code": response.status_code,
                "duration_ms": duration_ms,
            }
        )

        return response

    def _get_client_ip(self, request: Request) -> str:
        """Get client IP from headers or connection"""
        forwarded_for = request.headers.get("X-Forwarded-For")
        if forwarded_for:
            return forwarded_for.split(",")[0].strip()
        real_ip = request.headers.get("X-Real-IP")
        if real_ip:
            return real_ip
        return request.client.host if request.client else "unknown"
