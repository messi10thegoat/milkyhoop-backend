"""
Encrypted Fields Utility
========================
Provides decorators and utilities for transparent FLE on model fields.

Usage:
    from services.crypto.encrypted_fields import EncryptedModel, encrypted_field

    class User(EncryptedModel):
        email = encrypted_field("email")
        phone = encrypted_field("phone")

    # Automatic encryption on save, decryption on read
    user = User(email="test@example.com", phone="+6281234567890")
    print(user.email)  # Returns decrypted value
"""
import functools
import logging
from typing import Optional, List, Dict, Any, Callable, TypeVar, Type
from dataclasses import dataclass, field

from .fle_service import get_fle, encrypt_field, decrypt_field, is_encrypted

logger = logging.getLogger(__name__)

T = TypeVar("T")


@dataclass
class EncryptedFieldConfig:
    """Configuration for an encrypted field"""
    field_name: str
    use_aad: bool = True
    mask_on_log: bool = True
    searchable: bool = False  # If True, also store blind index


class EncryptedFieldDescriptor:
    """
    Descriptor for encrypted field access.
    Provides transparent encryption/decryption.
    """

    def __init__(self, field_name: str, config: Optional[EncryptedFieldConfig] = None):
        self.field_name = field_name
        self.config = config or EncryptedFieldConfig(field_name=field_name)
        self._storage_name = f"_encrypted_{field_name}"

    def __set_name__(self, owner, name):
        self.public_name = name

    def __get__(self, obj, objtype=None):
        if obj is None:
            return self

        encrypted_value = getattr(obj, self._storage_name, None)
        if not encrypted_value:
            return None

        try:
            aad = self.field_name if self.config.use_aad else None
            return decrypt_field(encrypted_value, aad)
        except Exception as e:
            logger.error(f"Failed to decrypt {self.field_name}: {e}")
            return None

    def __set__(self, obj, value):
        if value is None:
            setattr(obj, self._storage_name, None)
            return

        # Don't re-encrypt already encrypted values
        if is_encrypted(value):
            setattr(obj, self._storage_name, value)
            return

        try:
            aad = self.field_name if self.config.use_aad else None
            encrypted = encrypt_field(value, aad)
            setattr(obj, self._storage_name, encrypted)
        except Exception as e:
            logger.error(f"Failed to encrypt {self.field_name}: {e}")
            raise


def encrypted_field(
    field_name: Optional[str] = None,
    use_aad: bool = True,
    mask_on_log: bool = True,
    searchable: bool = False
) -> EncryptedFieldDescriptor:
    """
    Decorator factory for encrypted fields.

    Args:
        field_name: Name for AAD (defaults to attribute name)
        use_aad: Whether to use field name as additional authenticated data
        mask_on_log: Whether to mask this field in logs
        searchable: Whether to create a blind index for searching

    Returns:
        EncryptedFieldDescriptor instance
    """
    config = EncryptedFieldConfig(
        field_name=field_name or "",
        use_aad=use_aad,
        mask_on_log=mask_on_log,
        searchable=searchable
    )
    return EncryptedFieldDescriptor(field_name or "", config)


# ==============================================
# PII FIELDS DEFINITION
# ==============================================

# Fields that require encryption under UU PDP & PCI-DSS
PII_FIELDS = {
    "User": [
        "email",
        "name",
        "fullname",
        "nickname",
    ],
    "UserProfile": [
        "phoneNumber",
        "digitalSignature",
    ],
    "UserBusiness": [
        "taxId",
        "businessLicense",
    ],
    "UserFinance": [
        "paymentMethods",  # JSON field
    ],
    "TransaksiHarian": [
        "namaPihak",
        "kontakPihak",
    ],
    "Supplier": [
        "kontak",
    ],
    "Order": [
        "customer_name",
    ],
}


def mask_pii(value: str, visible_chars: int = 4) -> str:
    """
    Mask a PII value for logging.

    Examples:
        mask_pii("test@example.com") -> "test***@example.com"
        mask_pii("+6281234567890") -> "+628****7890"
    """
    if not value:
        return "***"

    length = len(value)

    if "@" in value:
        # Email: show first part partially
        local, domain = value.split("@", 1)
        if len(local) > 2:
            masked_local = local[:2] + "*" * (len(local) - 2)
        else:
            masked_local = "*" * len(local)
        return f"{masked_local}@{domain}"

    if length <= visible_chars * 2:
        # Short string: show only first and last char
        return value[0] + "*" * (length - 2) + value[-1] if length > 2 else "*" * length

    # Default: show first and last few chars
    return value[:visible_chars] + "*" * (length - visible_chars * 2) + value[-visible_chars:]


def get_blind_index(value: str, salt: Optional[str] = None) -> str:
    """
    Generate a blind index for searchable encryption.

    This allows searching encrypted fields without decrypting all records.
    Uses HMAC-SHA256 with a secret salt.
    """
    import hmac
    import hashlib
    import os

    if not value:
        return ""

    # Get salt from environment or use provided
    index_salt = salt or os.getenv("FLE_BLIND_INDEX_SALT", "default-blind-index-salt")

    # Normalize value (lowercase, strip whitespace)
    normalized = value.lower().strip()

    # Generate HMAC
    h = hmac.new(
        index_salt.encode(),
        normalized.encode(),
        hashlib.sha256
    )

    return h.hexdigest()


# ==============================================
# ENCRYPTION HELPERS FOR DICT/JSON DATA
# ==============================================

def encrypt_dict_fields(
    data: Dict[str, Any],
    fields_to_encrypt: List[str],
    prefix: str = ""
) -> Dict[str, Any]:
    """
    Encrypt specific fields in a dictionary.

    Args:
        data: Dictionary containing data
        fields_to_encrypt: List of field names to encrypt
        prefix: Prefix for AAD (e.g., table name)

    Returns:
        Dictionary with encrypted fields
    """
    result = data.copy()

    for field_name in fields_to_encrypt:
        if field_name in result and result[field_name]:
            value = result[field_name]
            if isinstance(value, str) and not is_encrypted(value):
                aad = f"{prefix}.{field_name}" if prefix else field_name
                result[field_name] = encrypt_field(value, aad)

    return result


def decrypt_dict_fields(
    data: Dict[str, Any],
    fields_to_decrypt: List[str],
    prefix: str = ""
) -> Dict[str, Any]:
    """
    Decrypt specific fields in a dictionary.

    Args:
        data: Dictionary containing encrypted data
        fields_to_decrypt: List of field names to decrypt
        prefix: Prefix for AAD (must match encryption)

    Returns:
        Dictionary with decrypted fields
    """
    result = data.copy()

    for field_name in fields_to_decrypt:
        if field_name in result and result[field_name]:
            value = result[field_name]
            if isinstance(value, str) and is_encrypted(value):
                aad = f"{prefix}.{field_name}" if prefix else field_name
                try:
                    result[field_name] = decrypt_field(value, aad)
                except Exception as e:
                    logger.error(f"Failed to decrypt {field_name}: {e}")
                    result[field_name] = None

    return result


# ==============================================
# DECORATOR FOR AUTOMATIC PII ENCRYPTION
# ==============================================

def encrypt_pii_on_save(model_name: str):
    """
    Decorator for functions that save data.
    Automatically encrypts PII fields before saving.

    Usage:
        @encrypt_pii_on_save("User")
        async def create_user(data: dict):
            # data is automatically encrypted
            return await db.user.create(data=data)
    """
    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            # Find the data argument
            if args:
                data = args[0] if isinstance(args[0], dict) else None
            else:
                data = kwargs.get("data") or kwargs.get("values")

            if data and isinstance(data, dict):
                pii_fields = PII_FIELDS.get(model_name, [])
                encrypted_data = encrypt_dict_fields(data, pii_fields, model_name)

                # Replace the data argument
                if args and isinstance(args[0], dict):
                    args = (encrypted_data,) + args[1:]
                elif "data" in kwargs:
                    kwargs["data"] = encrypted_data
                elif "values" in kwargs:
                    kwargs["values"] = encrypted_data

            return await func(*args, **kwargs)
        return wrapper
    return decorator


def decrypt_pii_on_read(model_name: str):
    """
    Decorator for functions that read data.
    Automatically decrypts PII fields after reading.

    Usage:
        @decrypt_pii_on_read("User")
        async def get_user(user_id: str):
            return await db.user.find_unique(where={"id": user_id})
    """
    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            result = await func(*args, **kwargs)

            if result is None:
                return None

            pii_fields = PII_FIELDS.get(model_name, [])

            if isinstance(result, dict):
                return decrypt_dict_fields(result, pii_fields, model_name)
            elif isinstance(result, list):
                return [
                    decrypt_dict_fields(item, pii_fields, model_name)
                    if isinstance(item, dict) else item
                    for item in result
                ]
            elif hasattr(result, "__dict__"):
                # Object with attributes
                for field_name in pii_fields:
                    if hasattr(result, field_name):
                        value = getattr(result, field_name)
                        if value and is_encrypted(value):
                            aad = f"{model_name}.{field_name}"
                            try:
                                decrypted = decrypt_field(value, aad)
                                setattr(result, field_name, decrypted)
                            except Exception as e:
                                logger.error(f"Failed to decrypt {field_name}: {e}")

            return result
        return wrapper
    return decorator
