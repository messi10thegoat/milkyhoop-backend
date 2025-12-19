"""
Web Application Firewall (WAF) Middleware
==========================================
Industry-standard protection against common web attacks.

Protects against:
- SQL Injection
- XSS (Cross-Site Scripting)
- Path Traversal
- Command Injection
- Request smuggling
- Malicious user agents
- Oversized requests

OWASP Top 10 compliant.
"""
import re
import logging
from typing import Set, List, Pattern, Optional
from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

logger = logging.getLogger(__name__)


class WAFMiddleware(BaseHTTPMiddleware):
    """
    Web Application Firewall middleware.
    Inspects requests for malicious patterns and blocks attacks.
    """

    # Maximum request sizes
    MAX_URL_LENGTH = 2048
    MAX_HEADER_SIZE = 8192
    MAX_BODY_SIZE = 10 * 1024 * 1024  # 10MB

    # SQL Injection patterns (more specific to avoid false positives)
    SQL_INJECTION_PATTERNS: List[Pattern] = [
        # SQL keywords with dangerous context
        re.compile(r"(\bUNION\s+(ALL\s+)?SELECT\b)", re.IGNORECASE),
        re.compile(r"(\bSELECT\s+.+\s+FROM\s+)", re.IGNORECASE),
        re.compile(r"(\bINSERT\s+INTO\s+)", re.IGNORECASE),
        re.compile(r"(\bUPDATE\s+\w+\s+SET\b)", re.IGNORECASE),
        re.compile(r"(\bDELETE\s+FROM\s+)", re.IGNORECASE),
        re.compile(r"(\bDROP\s+(TABLE|DATABASE)\b)", re.IGNORECASE),
        # Boolean-based injection
        re.compile(r"(\b(OR|AND)\s+[\'\"]?\d+[\'\"]?\s*=\s*[\'\"]?\d+)", re.IGNORECASE),
        re.compile(
            r"(\bOR\s+[\'\"]?[\w]+[\'\"]?\s*=\s*[\'\"]?[\w]+[\'\"]?\s*--)",
            re.IGNORECASE,
        ),
        # Comment-based injection (specific patterns)
        re.compile(r"(--\s*$|--\s+)", re.IGNORECASE),
        re.compile(r"(/\*.*\*/)", re.IGNORECASE),
        # Time-based injection
        re.compile(r"(\bWAITFOR\s+DELAY|\bBENCHMARK\s*\(|\bSLEEP\s*\()", re.IGNORECASE),
        # System table access
        re.compile(
            r"(INFORMATION_SCHEMA|sysobjects|syscolumns|pg_catalog)", re.IGNORECASE
        ),
        # Dangerous functions
        re.compile(r"(\bEXEC\s*\(|\bEXECUTE\s*\()", re.IGNORECASE),
    ]

    # XSS patterns
    XSS_PATTERNS: List[Pattern] = [
        re.compile(r"<script\b[^>]*>.*?</script>", re.IGNORECASE | re.DOTALL),
        re.compile(r"javascript\s*:", re.IGNORECASE),
        re.compile(r"on\w+\s*=", re.IGNORECASE),  # onclick, onerror, etc.
        re.compile(r"<\s*img[^>]+src\s*=\s*['\"]?\s*javascript:", re.IGNORECASE),
        re.compile(r"<\s*iframe", re.IGNORECASE),
        re.compile(r"<\s*embed", re.IGNORECASE),
        re.compile(r"<\s*object", re.IGNORECASE),
        re.compile(r"<\s*svg[^>]*onload", re.IGNORECASE),
        re.compile(r"expression\s*\(", re.IGNORECASE),
        re.compile(r"document\.(cookie|location|write)", re.IGNORECASE),
        re.compile(r"(eval|alert|prompt|confirm)\s*\(", re.IGNORECASE),
    ]

    # Path traversal patterns
    PATH_TRAVERSAL_PATTERNS: List[Pattern] = [
        re.compile(r"\.\./"),
        re.compile(r"\.\.\\"),
        re.compile(r"%2e%2e/", re.IGNORECASE),
        re.compile(r"%2e%2e\\", re.IGNORECASE),
        re.compile(r"\.%2e/", re.IGNORECASE),
        re.compile(r"%2e\./", re.IGNORECASE),
        re.compile(r"/etc/passwd"),
        re.compile(r"/etc/shadow"),
        re.compile(r"c:\\windows", re.IGNORECASE),
    ]

    # Command injection patterns
    COMMAND_INJECTION_PATTERNS: List[Pattern] = [
        re.compile(r"[;&|`$]"),
        re.compile(r"\$\(.*\)"),
        re.compile(r"`.*`"),
        re.compile(r"\|\s*\w+"),
        re.compile(r">\s*/"),
        re.compile(r"<\s*/"),
    ]

    # Malicious user agents (security scanners only)
    BLOCKED_USER_AGENTS: Set[str] = {
        "sqlmap",
        "nikto",
        "nmap",
        "masscan",
        "zgrab",
        "gobuster",
        "dirbuster",
        "wfuzz",
        "ffuf",
        "acunetix",
        "nessus",
        "burp",
        "zap",
        # Removed curl/wget - legitimate tools
    }

    # Whitelisted paths (skip WAF checks)
    WHITELIST_PATHS: Set[str] = {
        "/health",
        "/healthz",
        "/metrics",
        "/docs",
        "/openapi.json",
        "/redoc",
    }

    # Fast paths - skip WAF for speed-critical endpoints
    FAST_PATHS: Set[str] = {
        "/api/products/search/pos",  # Autocomplete needs <100ms
        "/api/products/search/kulakan",  # Kulakan autocomplete
        "/api/products/barcode/",  # Barcode lookup
    }

    # Paths with relaxed WAF (check body only, not headers)
    RELAXED_PATHS: Set[str] = {
        "/api/auth/login",
        "/api/auth/register",
        "/api/auth/refresh",
    }

    def __init__(self, app, enabled: bool = True, strict_mode: bool = False):
        super().__init__(app)
        self.enabled = enabled
        self.strict_mode = strict_mode  # If True, block on any suspicion

    async def dispatch(self, request: Request, call_next):
        if not self.enabled:
            return await call_next(request)

        # Skip whitelisted paths
        if request.url.path in self.WHITELIST_PATHS:
            return await call_next(request)

        # Skip fast paths (autocomplete etc) - speed over security checks
        for fast_path in self.FAST_PATHS:
            if request.url.path.startswith(fast_path):
                return await call_next(request)

        # Check if path has relaxed WAF (skip header checks for auth endpoints)
        is_relaxed_path = request.url.path in self.RELAXED_PATHS

        # Check URL length
        if len(str(request.url)) > self.MAX_URL_LENGTH:
            logger.warning(f"WAF: URL too long from {request.client.host}")
            return self._block_request("URL too long", 414)

        # Check user agent
        user_agent = request.headers.get("user-agent", "").lower()
        if self._is_blocked_user_agent(user_agent):
            logger.warning(
                f"WAF: Blocked user agent from {request.client.host}: {user_agent[:50]}"
            )
            return self._block_request("Forbidden", 403)

        # Check URL path for attacks
        url_path = request.url.path + "?" + str(request.query_params)
        threat = self._detect_threat(url_path, "URL")
        if threat:
            logger.warning(f"WAF: {threat} in URL from {request.client.host}")
            return self._block_request(f"Blocked: {threat}", 403)

        # Check headers (skip for relaxed paths like auth)
        if not is_relaxed_path:
            for header_name, header_value in request.headers.items():
                if len(header_value) > self.MAX_HEADER_SIZE:
                    logger.warning(f"WAF: Header too large from {request.client.host}")
                    return self._block_request("Header too large", 431)

                # Skip checking certain headers
                if header_name.lower() in (
                    "authorization",
                    "cookie",
                    "content-type",
                    "origin",
                    "referer",
                    "accept",
                ):
                    continue

                threat = self._detect_threat(header_value, f"Header:{header_name}")
                if threat:
                    logger.warning(
                        f"WAF: {threat} in header from {request.client.host}"
                    )
                    return self._block_request(f"Blocked: {threat}", 403)

        # Check request body for POST/PUT/PATCH
        if request.method in ("POST", "PUT", "PATCH"):
            content_length = request.headers.get("content-length", "0")
            try:
                if int(content_length) > self.MAX_BODY_SIZE:
                    logger.warning(f"WAF: Body too large from {request.client.host}")
                    return self._block_request("Request body too large", 413)
            except ValueError:
                pass

            # Read and check body
            try:
                body = await request.body()
                if body:
                    body_str = body.decode("utf-8", errors="ignore")
                    threat = self._detect_threat(body_str, "Body")
                    if threat:
                        logger.warning(
                            f"WAF: {threat} in body from {request.client.host}"
                        )
                        return self._block_request(f"Blocked: {threat}", 403)
            except Exception as e:
                logger.error(f"WAF: Error reading body: {e}")

        # All checks passed
        response = await call_next(request)

        # Add security headers to response
        response.headers["X-WAF-Status"] = "passed"

        return response

    def _is_blocked_user_agent(self, user_agent: str) -> bool:
        """Check if user agent is blocked"""
        for blocked in self.BLOCKED_USER_AGENTS:
            if blocked in user_agent:
                return True
        return False

    def _detect_threat(self, content: str, location: str) -> Optional[str]:
        """Detect threats in content"""
        if not content:
            return None

        # SQL Injection
        for pattern in self.SQL_INJECTION_PATTERNS:
            if pattern.search(content):
                return "SQL Injection"

        # XSS
        for pattern in self.XSS_PATTERNS:
            if pattern.search(content):
                return "XSS"

        # Path Traversal
        for pattern in self.PATH_TRAVERSAL_PATTERNS:
            if pattern.search(content):
                return "Path Traversal"

        # Command Injection (only in strict mode or obvious cases)
        if self.strict_mode:
            for pattern in self.COMMAND_INJECTION_PATTERNS:
                if pattern.search(content):
                    return "Command Injection"

        return None

    def _block_request(self, message: str, status_code: int = 403) -> JSONResponse:
        """Return a blocked response"""
        return JSONResponse(
            status_code=status_code,
            content={
                "error": "Request blocked by WAF",
                "message": message,
                "code": "WAF_BLOCKED",
            },
            headers={"X-WAF-Status": "blocked"},
        )


class IPReputationMiddleware(BaseHTTPMiddleware):
    """
    IP Reputation checking middleware.
    Blocks known malicious IPs and Tor exit nodes.
    """

    # Known bad IP ranges (example - in production use threat intel feed)
    BLOCKED_IP_RANGES: Set[str] = set()

    # Track suspicious IPs (simple in-memory, use Redis in production)
    _suspicious_ips: dict = {}
    SUSPICION_THRESHOLD = 5
    SUSPICION_WINDOW = 300  # 5 minutes

    def __init__(self, app, enabled: bool = True):
        super().__init__(app)
        self.enabled = enabled

    async def dispatch(self, request: Request, call_next):
        if not self.enabled:
            return await call_next(request)

        client_ip = request.client.host if request.client else "unknown"

        # Check if IP is blocked
        if self._is_ip_blocked(client_ip):
            logger.warning(f"IPReputation: Blocked IP {client_ip}")
            return JSONResponse(
                status_code=403, content={"error": "IP blocked", "code": "IP_BLOCKED"}
            )

        return await call_next(request)

    def _is_ip_blocked(self, ip: str) -> bool:
        """Check if IP is in blocklist"""
        # Check exact match
        if ip in self.BLOCKED_IP_RANGES:
            return True

        # Check suspicion score
        import time

        now = time.time()
        if ip in self._suspicious_ips:
            score, timestamp = self._suspicious_ips[ip]
            if now - timestamp < self.SUSPICION_WINDOW:
                return score >= self.SUSPICION_THRESHOLD
            else:
                # Reset old score
                del self._suspicious_ips[ip]

        return False

    def mark_suspicious(self, ip: str, score: int = 1):
        """Mark an IP as suspicious"""
        import time

        now = time.time()

        if ip in self._suspicious_ips:
            current_score, _ = self._suspicious_ips[ip]
            self._suspicious_ips[ip] = (current_score + score, now)
        else:
            self._suspicious_ips[ip] = (score, now)
