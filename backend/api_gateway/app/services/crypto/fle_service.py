"""
Field-Level Encryption (FLE) Service
=====================================
Production-grade envelope encryption for sensitive data.

Implements:
- AES-256-GCM for data encryption
- Envelope encryption pattern (DEK + KEK)
- Key rotation support
- PCI-DSS and UU PDP compliance

Architecture:
- Data Encryption Key (DEK): Random 256-bit key per encryption
- Key Encryption Key (KEK): Master key that encrypts DEKs
- Encrypted format: version:nonce:encrypted_dek:ciphertext:tag

For HashiCorp Vault Transit integration, see vault_kms.py
"""
import os
import base64
import hashlib
import secrets
import logging
from typing import Optional, Tuple, Dict, Any
from datetime import datetime, timezone
from dataclasses import dataclass
from enum import Enum

# Cryptography imports
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.backends import default_backend

logger = logging.getLogger(__name__)


class FLEVersion(str, Enum):
    """Encryption format versions for forward compatibility"""
    V1 = "v1"  # AES-256-GCM with local KEK


@dataclass
class EncryptedValue:
    """Structured representation of encrypted data"""
    version: str
    nonce: bytes
    encrypted_dek: bytes
    ciphertext: bytes
    tag: bytes
    key_id: str
    encrypted_at: str

    def to_string(self) -> str:
        """Serialize to storable string format"""
        # Format: version:key_id:nonce_b64:encrypted_dek_b64:ciphertext_b64:tag_b64:timestamp
        return ":".join([
            self.version,
            self.key_id,
            base64.urlsafe_b64encode(self.nonce).decode(),
            base64.urlsafe_b64encode(self.encrypted_dek).decode(),
            base64.urlsafe_b64encode(self.ciphertext).decode(),
            base64.urlsafe_b64encode(self.tag).decode(),
            self.encrypted_at
        ])

    @classmethod
    def from_string(cls, value: str) -> "EncryptedValue":
        """Deserialize from stored string format"""
        # Split only first 6 colons, rest is timestamp (which may contain colons)
        parts = value.split(":", 6)
        if len(parts) != 7:
            raise ValueError(f"Invalid encrypted value format: expected 7 parts, got {len(parts)}")

        return cls(
            version=parts[0],
            key_id=parts[1],
            nonce=base64.urlsafe_b64decode(parts[2]),
            encrypted_dek=base64.urlsafe_b64decode(parts[3]),
            ciphertext=base64.urlsafe_b64decode(parts[4]),
            tag=base64.urlsafe_b64decode(parts[5]),
            encrypted_at=parts[6]
        )


class KeyStore:
    """
    In-memory key store for KEKs.
    In production, replace with HashiCorp Vault Transit.
    """

    def __init__(self):
        self._keys: Dict[str, bytes] = {}
        self._active_key_id: Optional[str] = None

    def add_key(self, key_id: str, key: bytes, set_active: bool = False):
        """Add a KEK to the store"""
        if len(key) != 32:
            raise ValueError("KEK must be 256 bits (32 bytes)")
        self._keys[key_id] = key
        if set_active or self._active_key_id is None:
            self._active_key_id = key_id
        logger.info(f"Added key: {key_id}, active: {set_active}")

    def get_key(self, key_id: str) -> Optional[bytes]:
        """Retrieve a KEK by ID"""
        return self._keys.get(key_id)

    @property
    def active_key_id(self) -> Optional[str]:
        """Get the currently active key ID"""
        return self._active_key_id

    @property
    def active_key(self) -> Optional[bytes]:
        """Get the currently active KEK"""
        if self._active_key_id:
            return self._keys.get(self._active_key_id)
        return None


class FieldLevelEncryption:
    """
    Field-Level Encryption service using envelope encryption.

    Security properties:
    - Each field gets a unique DEK (defense in depth)
    - DEKs are encrypted with KEK (key hierarchy)
    - AES-256-GCM provides authenticated encryption
    - Nonce is randomly generated for each encryption
    """

    NONCE_SIZE = 12  # 96 bits for GCM
    KEY_SIZE = 32    # 256 bits
    TAG_SIZE = 16    # 128 bits

    def __init__(self, key_store: Optional[KeyStore] = None):
        """
        Initialize FLE service.

        Args:
            key_store: KeyStore instance. If None, creates one from env vars.
        """
        self.key_store = key_store or self._init_default_keystore()
        self._aesgcm_cache: Dict[str, AESGCM] = {}

    def _init_default_keystore(self) -> KeyStore:
        """Initialize key store from environment variables"""
        store = KeyStore()

        # Primary KEK from environment
        primary_kek = os.getenv("FLE_PRIMARY_KEK")
        if primary_kek:
            key = self._derive_key_from_secret(primary_kek)
            store.add_key("primary", key, set_active=True)
            logger.info("Loaded primary KEK from environment")

        # Secondary KEK for rotation
        secondary_kek = os.getenv("FLE_SECONDARY_KEK")
        if secondary_kek:
            key = self._derive_key_from_secret(secondary_kek)
            store.add_key("secondary", key)
            logger.info("Loaded secondary KEK from environment")

        # Fallback for development (NOT FOR PRODUCTION)
        if not store.active_key_id:
            logger.warning("No FLE_PRIMARY_KEK set! Using development key. DO NOT USE IN PRODUCTION!")
            dev_key = self._derive_key_from_secret("dev-only-insecure-key-change-me")
            store.add_key("dev", dev_key, set_active=True)

        return store

    def _derive_key_from_secret(self, secret: str, salt: Optional[bytes] = None) -> bytes:
        """
        Derive a 256-bit key from a secret using PBKDF2.

        Args:
            secret: The secret string
            salt: Salt for key derivation. If None, uses a deterministic salt.
        """
        if salt is None:
            # Use a deterministic salt based on app identifier
            salt = hashlib.sha256(b"milkyhoop-fle-kek-salt").digest()

        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=self.KEY_SIZE,
            salt=salt,
            iterations=600_000,  # OWASP 2023 recommendation
            backend=default_backend()
        )
        return kdf.derive(secret.encode())

    def _get_aesgcm(self, key: bytes) -> AESGCM:
        """Get or create AESGCM instance (cached for performance)"""
        key_hash = hashlib.sha256(key).hexdigest()[:16]
        if key_hash not in self._aesgcm_cache:
            self._aesgcm_cache[key_hash] = AESGCM(key)
        return self._aesgcm_cache[key_hash]

    def encrypt(
        self,
        plaintext: str,
        associated_data: Optional[str] = None
    ) -> str:
        """
        Encrypt a plaintext value using envelope encryption.

        Args:
            plaintext: The value to encrypt
            associated_data: Additional data to authenticate (e.g., field name, record ID)

        Returns:
            Encrypted value as a string (safe for DB storage)

        Raises:
            ValueError: If encryption fails
        """
        if not plaintext:
            return ""

        kek = self.key_store.active_key
        key_id = self.key_store.active_key_id

        if not kek or not key_id:
            raise ValueError("No active encryption key available")

        try:
            # 1. Generate random DEK (Data Encryption Key)
            dek = secrets.token_bytes(self.KEY_SIZE)

            # 2. Generate random nonce
            nonce = secrets.token_bytes(self.NONCE_SIZE)

            # 3. Encrypt plaintext with DEK
            dek_aesgcm = AESGCM(dek)
            aad = associated_data.encode() if associated_data else None
            ciphertext_with_tag = dek_aesgcm.encrypt(nonce, plaintext.encode(), aad)

            # Split ciphertext and tag
            ciphertext = ciphertext_with_tag[:-self.TAG_SIZE]
            tag = ciphertext_with_tag[-self.TAG_SIZE:]

            # 4. Encrypt DEK with KEK
            kek_aesgcm = self._get_aesgcm(kek)
            dek_nonce = secrets.token_bytes(self.NONCE_SIZE)
            encrypted_dek = kek_aesgcm.encrypt(dek_nonce, dek, None)

            # Combine DEK nonce with encrypted DEK
            encrypted_dek_full = dek_nonce + encrypted_dek

            # 5. Create structured encrypted value
            encrypted_value = EncryptedValue(
                version=FLEVersion.V1.value,
                key_id=key_id,
                nonce=nonce,
                encrypted_dek=encrypted_dek_full,
                ciphertext=ciphertext,
                tag=tag,
                encrypted_at=datetime.now(timezone.utc).isoformat()
            )

            return encrypted_value.to_string()

        except Exception as e:
            logger.error(f"Encryption failed: {e}")
            raise ValueError(f"Encryption failed: {e}") from e

    def decrypt(
        self,
        encrypted_value: str,
        associated_data: Optional[str] = None
    ) -> str:
        """
        Decrypt an encrypted value.

        Args:
            encrypted_value: The encrypted string from encrypt()
            associated_data: Same AAD used during encryption

        Returns:
            Decrypted plaintext

        Raises:
            ValueError: If decryption fails or data is tampered
        """
        if not encrypted_value:
            return ""

        try:
            # 1. Parse encrypted value
            ev = EncryptedValue.from_string(encrypted_value)

            # 2. Get the KEK used for this encryption
            kek = self.key_store.get_key(ev.key_id)
            if not kek:
                raise ValueError(f"Key not found: {ev.key_id}")

            # 3. Decrypt DEK
            kek_aesgcm = self._get_aesgcm(kek)
            dek_nonce = ev.encrypted_dek[:self.NONCE_SIZE]
            encrypted_dek = ev.encrypted_dek[self.NONCE_SIZE:]
            dek = kek_aesgcm.decrypt(dek_nonce, encrypted_dek, None)

            # 4. Decrypt ciphertext with DEK
            dek_aesgcm = AESGCM(dek)
            aad = associated_data.encode() if associated_data else None
            ciphertext_with_tag = ev.ciphertext + ev.tag
            plaintext = dek_aesgcm.decrypt(ev.nonce, ciphertext_with_tag, aad)

            return plaintext.decode()

        except Exception as e:
            logger.error(f"Decryption failed: {e}")
            raise ValueError(f"Decryption failed: {e}") from e

    def is_encrypted(self, value: str) -> bool:
        """Check if a value appears to be encrypted"""
        if not value:
            return False
        try:
            parts = value.split(":")
            return len(parts) == 7 and parts[0] in [v.value for v in FLEVersion]
        except Exception:
            return False

    def rotate_key(self, encrypted_value: str, associated_data: Optional[str] = None) -> str:
        """
        Re-encrypt a value with the current active key.

        Args:
            encrypted_value: Previously encrypted value
            associated_data: Same AAD used during encryption

        Returns:
            Newly encrypted value with current active key
        """
        plaintext = self.decrypt(encrypted_value, associated_data)
        return self.encrypt(plaintext, associated_data)


# Global singleton instance
_fle_instance: Optional[FieldLevelEncryption] = None


def get_fle() -> FieldLevelEncryption:
    """Get or create the global FLE instance"""
    global _fle_instance
    if _fle_instance is None:
        _fle_instance = FieldLevelEncryption()
    return _fle_instance


# Convenience functions
def encrypt_field(value: str, field_name: Optional[str] = None) -> str:
    """Encrypt a field value"""
    return get_fle().encrypt(value, associated_data=field_name)


def decrypt_field(value: str, field_name: Optional[str] = None) -> str:
    """Decrypt a field value"""
    return get_fle().decrypt(value, associated_data=field_name)


def is_encrypted(value: str) -> bool:
    """Check if a value is encrypted"""
    return get_fle().is_encrypted(value)
