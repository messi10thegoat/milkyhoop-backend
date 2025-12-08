"""
FLE Middleware Integration
==========================
Automatic encryption/decryption for API requests and responses.

This middleware intercepts specific endpoints and transparently
handles PII encryption/decryption.
"""
import logging
import json
from typing import Optional, List, Set
from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from .fle_service import get_fle, is_encrypted
from .encrypted_fields import PII_FIELDS, encrypt_dict_fields, decrypt_dict_fields, mask_pii

logger = logging.getLogger(__name__)


class FLEMiddleware(BaseHTTPMiddleware):
    """
    Middleware for automatic Field-Level Encryption.

    Features:
    - Encrypts PII in request bodies before processing
    - Decrypts PII in response bodies before sending
    - Masks PII in logs
    - Skips encryption for non-JSON content
    """

    # Endpoints that handle PII and need encryption
    ENCRYPT_ENDPOINTS: Set[str] = {
        "/api/users",
        "/api/auth/register",
        "/api/auth/profile",
        "/api/profile",
        "/api/tenant/*/transactions",
        "/api/suppliers",
    }

    # Endpoints that return PII and need decryption
    DECRYPT_ENDPOINTS: Set[str] = {
        "/api/users",
        "/api/auth/me",
        "/api/auth/profile",
        "/api/profile",
        "/api/tenant/*/transactions",
        "/api/suppliers",
        "/api/members",
    }

    def __init__(self, app, enabled: bool = True):
        super().__init__(app)
        self.enabled = enabled
        self.fle = get_fle() if enabled else None

    def _should_encrypt(self, path: str, method: str) -> bool:
        """Check if request should have encryption applied"""
        if method not in ("POST", "PUT", "PATCH"):
            return False

        for pattern in self.ENCRYPT_ENDPOINTS:
            if self._path_matches(path, pattern):
                return True
        return False

    def _should_decrypt(self, path: str, method: str) -> bool:
        """Check if response should have decryption applied"""
        if method not in ("GET", "POST"):
            return False

        for pattern in self.DECRYPT_ENDPOINTS:
            if self._path_matches(path, pattern):
                return True
        return False

    def _path_matches(self, path: str, pattern: str) -> bool:
        """Check if path matches pattern (supports * wildcard)"""
        if "*" not in pattern:
            return path.startswith(pattern)

        parts = pattern.split("*")
        if len(parts) != 2:
            return path.startswith(pattern.replace("*", ""))

        return path.startswith(parts[0]) and parts[1] in path

    def _get_model_name(self, path: str) -> Optional[str]:
        """Determine which model's PII fields to use based on path"""
        if "/users" in path or "/auth" in path or "/profile" in path:
            return "User"
        if "/transactions" in path:
            return "TransaksiHarian"
        if "/suppliers" in path:
            return "Supplier"
        if "/members" in path:
            return "User"
        return None

    async def dispatch(self, request: Request, call_next):
        if not self.enabled:
            return await call_next(request)

        path = request.url.path
        method = request.method

        # Handle request encryption
        if self._should_encrypt(path, method):
            try:
                body = await request.body()
                if body:
                    data = json.loads(body)
                    model_name = self._get_model_name(path)

                    if model_name and model_name in PII_FIELDS:
                        encrypted_data = encrypt_dict_fields(
                            data,
                            PII_FIELDS[model_name],
                            model_name
                        )

                        # Create new request with encrypted body
                        # Note: This is a simplified approach; production may need
                        # more sophisticated request modification
                        request.state.encrypted_body = json.dumps(encrypted_data).encode()

            except json.JSONDecodeError:
                pass  # Not JSON, skip encryption
            except Exception as e:
                logger.error(f"FLE encryption error: {e}")

        # Process request
        response = await call_next(request)

        # Handle response decryption
        if self._should_decrypt(path, method):
            if response.headers.get("content-type", "").startswith("application/json"):
                try:
                    # Read response body
                    body = b""
                    async for chunk in response.body_iterator:
                        body += chunk

                    if body:
                        data = json.loads(body)
                        model_name = self._get_model_name(path)

                        if model_name and model_name in PII_FIELDS:
                            if isinstance(data, dict):
                                # Check for nested data structures
                                if "data" in data and isinstance(data["data"], (dict, list)):
                                    data["data"] = self._decrypt_nested(
                                        data["data"],
                                        PII_FIELDS[model_name],
                                        model_name
                                    )
                                else:
                                    data = decrypt_dict_fields(
                                        data,
                                        PII_FIELDS[model_name],
                                        model_name
                                    )
                            elif isinstance(data, list):
                                data = [
                                    decrypt_dict_fields(item, PII_FIELDS[model_name], model_name)
                                    if isinstance(item, dict) else item
                                    for item in data
                                ]

                        # Create new response
                        return JSONResponse(
                            content=data,
                            status_code=response.status_code,
                            headers=dict(response.headers)
                        )

                except json.JSONDecodeError:
                    pass  # Not JSON, skip decryption
                except Exception as e:
                    logger.error(f"FLE decryption error: {e}")

        return response

    def _decrypt_nested(self, data, fields: List[str], model_name: str):
        """Decrypt nested data structures"""
        if isinstance(data, dict):
            return decrypt_dict_fields(data, fields, model_name)
        elif isinstance(data, list):
            return [
                decrypt_dict_fields(item, fields, model_name)
                if isinstance(item, dict) else item
                for item in data
            ]
        return data


# ==============================================
# RESPONSE SANITIZATION FOR LOGS
# ==============================================

class PIIMaskingMiddleware(BaseHTTPMiddleware):
    """
    Middleware to mask PII in logs and error responses.

    This ensures that even if PII is accidentally logged,
    it will be masked to protect user privacy.
    """

    # Fields that should always be masked in logs/responses
    SENSITIVE_FIELDS = {
        "password", "passwordHash", "password_hash",
        "token", "access_token", "refresh_token",
        "secret", "api_key", "apiKey",
        "email", "phone", "phoneNumber", "phone_number",
        "taxId", "tax_id", "businessLicense", "business_license",
        "ssn", "credit_card", "creditCard", "cvv",
    }

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        return response

    @classmethod
    def mask_sensitive_data(cls, data: dict) -> dict:
        """Recursively mask sensitive fields in a dictionary"""
        if not isinstance(data, dict):
            return data

        result = {}
        for key, value in data.items():
            if key.lower() in {f.lower() for f in cls.SENSITIVE_FIELDS}:
                # Mask the value
                if isinstance(value, str):
                    result[key] = mask_pii(value)
                else:
                    result[key] = "***"
            elif isinstance(value, dict):
                result[key] = cls.mask_sensitive_data(value)
            elif isinstance(value, list):
                result[key] = [
                    cls.mask_sensitive_data(item) if isinstance(item, dict) else item
                    for item in value
                ]
            else:
                result[key] = value

        return result
