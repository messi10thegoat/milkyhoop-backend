"""
HashiCorp Vault Transit KMS Integration
========================================
Production-grade Key Management Service using Vault Transit secrets engine.

Features:
- Hardware-backed key storage (when using Vault Enterprise + HSM)
- Automatic key rotation
- Audit logging of all key operations
- High availability

Prerequisites:
1. HashiCorp Vault server with Transit engine enabled
2. Policy with encrypt/decrypt permissions for the transit key
3. Environment variables: VAULT_ADDR, VAULT_TOKEN

Setup:
    vault secrets enable transit
    vault write transit/keys/milkyhoop-fle type=aes256-gcm96 auto_rotate_period=90d
"""
import os
import base64
import logging
import aiohttp
from typing import Optional, Dict, Any
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class VaultConfig:
    """Vault configuration"""
    addr: str
    token: str
    key_name: str = "milkyhoop-fle"
    transit_path: str = "transit"
    timeout: int = 30
    verify_ssl: bool = True


class VaultTransitKMS:
    """
    Vault Transit KMS client for envelope encryption.

    This replaces the local KEK with Vault-managed keys,
    providing enterprise-grade key management.
    """

    def __init__(self, config: Optional[VaultConfig] = None):
        """
        Initialize Vault Transit client.

        Args:
            config: VaultConfig instance. If None, reads from environment.
        """
        self.config = config or self._config_from_env()
        self._session: Optional[aiohttp.ClientSession] = None

    def _config_from_env(self) -> VaultConfig:
        """Create config from environment variables"""
        addr = os.getenv("VAULT_ADDR", "http://127.0.0.1:8200")
        token = os.getenv("VAULT_TOKEN", "")

        if not token:
            logger.warning("VAULT_TOKEN not set. Vault KMS will not function.")

        return VaultConfig(
            addr=addr,
            token=token,
            key_name=os.getenv("VAULT_KEY_NAME", "milkyhoop-fle"),
            transit_path=os.getenv("VAULT_TRANSIT_PATH", "transit"),
            verify_ssl=os.getenv("VAULT_SKIP_VERIFY", "false").lower() != "true"
        )

    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create HTTP session"""
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=self.config.timeout)
            self._session = aiohttp.ClientSession(
                timeout=timeout,
                headers={"X-Vault-Token": self.config.token}
            )
        return self._session

    async def close(self):
        """Close HTTP session"""
        if self._session and not self._session.closed:
            await self._session.close()

    def _url(self, path: str) -> str:
        """Build Vault API URL"""
        return f"{self.config.addr}/v1/{self.config.transit_path}/{path}"

    async def encrypt_dek(self, dek: bytes) -> str:
        """
        Encrypt a DEK using Vault Transit.

        Args:
            dek: Data Encryption Key (raw bytes)

        Returns:
            Vault ciphertext (includes version for key rotation)
        """
        session = await self._get_session()

        # Base64 encode the DEK
        plaintext_b64 = base64.b64encode(dek).decode()

        url = self._url(f"encrypt/{self.config.key_name}")
        payload = {"plaintext": plaintext_b64}

        try:
            async with session.post(url, json=payload, ssl=self.config.verify_ssl) as resp:
                if resp.status != 200:
                    error = await resp.text()
                    raise Exception(f"Vault encrypt failed: {resp.status} - {error}")

                data = await resp.json()
                ciphertext = data["data"]["ciphertext"]
                logger.debug(f"Encrypted DEK with Vault key {self.config.key_name}")
                return ciphertext

        except aiohttp.ClientError as e:
            logger.error(f"Vault connection error: {e}")
            raise

    async def decrypt_dek(self, ciphertext: str) -> bytes:
        """
        Decrypt a DEK using Vault Transit.

        Args:
            ciphertext: Vault ciphertext from encrypt_dek()

        Returns:
            Decrypted DEK (raw bytes)
        """
        session = await self._get_session()

        url = self._url(f"decrypt/{self.config.key_name}")
        payload = {"ciphertext": ciphertext}

        try:
            async with session.post(url, json=payload, ssl=self.config.verify_ssl) as resp:
                if resp.status != 200:
                    error = await resp.text()
                    raise Exception(f"Vault decrypt failed: {resp.status} - {error}")

                data = await resp.json()
                plaintext_b64 = data["data"]["plaintext"]
                dek = base64.b64decode(plaintext_b64)
                logger.debug(f"Decrypted DEK with Vault key {self.config.key_name}")
                return dek

        except aiohttp.ClientError as e:
            logger.error(f"Vault connection error: {e}")
            raise

    async def rewrap_dek(self, ciphertext: str) -> str:
        """
        Re-encrypt a DEK with the latest key version.
        Use this during key rotation.

        Args:
            ciphertext: Existing Vault ciphertext

        Returns:
            New ciphertext encrypted with latest key version
        """
        session = await self._get_session()

        url = self._url(f"rewrap/{self.config.key_name}")
        payload = {"ciphertext": ciphertext}

        try:
            async with session.post(url, json=payload, ssl=self.config.verify_ssl) as resp:
                if resp.status != 200:
                    error = await resp.text()
                    raise Exception(f"Vault rewrap failed: {resp.status} - {error}")

                data = await resp.json()
                new_ciphertext = data["data"]["ciphertext"]
                logger.info(f"Rewrapped DEK with latest Vault key version")
                return new_ciphertext

        except aiohttp.ClientError as e:
            logger.error(f"Vault connection error: {e}")
            raise

    async def rotate_key(self) -> Dict[str, Any]:
        """
        Trigger key rotation in Vault.
        Creates a new key version; old versions remain for decryption.

        Returns:
            Key info after rotation
        """
        session = await self._get_session()

        url = self._url(f"keys/{self.config.key_name}/rotate")

        try:
            async with session.post(url, ssl=self.config.verify_ssl) as resp:
                if resp.status != 200 and resp.status != 204:
                    error = await resp.text()
                    raise Exception(f"Vault rotate failed: {resp.status} - {error}")

                # Get updated key info
                return await self.get_key_info()

        except aiohttp.ClientError as e:
            logger.error(f"Vault connection error: {e}")
            raise

    async def get_key_info(self) -> Dict[str, Any]:
        """
        Get information about the transit key.

        Returns:
            Key metadata including versions, rotation policy, etc.
        """
        session = await self._get_session()

        url = self._url(f"keys/{self.config.key_name}")

        try:
            async with session.get(url, ssl=self.config.verify_ssl) as resp:
                if resp.status != 200:
                    error = await resp.text()
                    raise Exception(f"Vault key info failed: {resp.status} - {error}")

                data = await resp.json()
                return data["data"]

        except aiohttp.ClientError as e:
            logger.error(f"Vault connection error: {e}")
            raise

    async def health_check(self) -> bool:
        """Check if Vault is healthy and accessible"""
        try:
            session = await self._get_session()
            url = f"{self.config.addr}/v1/sys/health"

            async with session.get(url, ssl=self.config.verify_ssl) as resp:
                return resp.status in (200, 429, 472, 473, 501, 503)
        except Exception as e:
            logger.error(f"Vault health check failed: {e}")
            return False


# ==============================================
# VAULT-BACKED FLE SERVICE
# ==============================================

class VaultFieldLevelEncryption:
    """
    Field-Level Encryption using Vault Transit for KEK management.

    This provides the same interface as FieldLevelEncryption but uses
    Vault for the key encryption layer.
    """

    NONCE_SIZE = 12
    KEY_SIZE = 32
    TAG_SIZE = 16

    def __init__(self, vault_config: Optional[VaultConfig] = None):
        self.vault = VaultTransitKMS(vault_config)
        self._local_fle = None  # Fallback to local FLE if Vault unavailable

    async def encrypt(
        self,
        plaintext: str,
        associated_data: Optional[str] = None
    ) -> str:
        """
        Encrypt using Vault-managed keys.

        The DEK is encrypted by Vault Transit; data encryption is local.
        """
        import secrets
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        from datetime import datetime, timezone

        if not plaintext:
            return ""

        try:
            # 1. Generate random DEK
            dek = secrets.token_bytes(self.KEY_SIZE)

            # 2. Generate random nonce
            nonce = secrets.token_bytes(self.NONCE_SIZE)

            # 3. Encrypt plaintext with DEK (local)
            dek_aesgcm = AESGCM(dek)
            aad = associated_data.encode() if associated_data else None
            ciphertext_with_tag = dek_aesgcm.encrypt(nonce, plaintext.encode(), aad)

            # 4. Encrypt DEK with Vault Transit
            vault_ciphertext = await self.vault.encrypt_dek(dek)

            # 5. Create output format
            # vault:nonce_b64:vault_ciphertext:ciphertext_b64:timestamp
            import base64
            output = ":".join([
                "vault",
                base64.urlsafe_b64encode(nonce).decode(),
                vault_ciphertext,
                base64.urlsafe_b64encode(ciphertext_with_tag).decode(),
                datetime.now(timezone.utc).isoformat()
            ])

            return output

        except Exception as e:
            logger.error(f"Vault encryption failed: {e}")
            raise

    async def decrypt(
        self,
        encrypted_value: str,
        associated_data: Optional[str] = None
    ) -> str:
        """Decrypt using Vault-managed keys."""
        import base64
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        if not encrypted_value:
            return ""

        try:
            # Parse format: vault:nonce_b64:vault_ciphertext:ciphertext_b64:timestamp
            parts = encrypted_value.split(":")

            # Handle Vault format (5 parts) or standard format
            if parts[0] == "vault" and len(parts) >= 5:
                nonce = base64.urlsafe_b64decode(parts[1])
                # Vault ciphertext might contain colons
                vault_ciphertext = ":".join(parts[2:-2])
                ciphertext_with_tag = base64.urlsafe_b64decode(parts[-2])

                # Decrypt DEK with Vault
                dek = await self.vault.decrypt_dek(vault_ciphertext)

                # Decrypt data with DEK
                dek_aesgcm = AESGCM(dek)
                aad = associated_data.encode() if associated_data else None
                plaintext = dek_aesgcm.decrypt(nonce, ciphertext_with_tag, aad)

                return plaintext.decode()
            else:
                raise ValueError("Not a Vault-encrypted value")

        except Exception as e:
            logger.error(f"Vault decryption failed: {e}")
            raise

    async def rewrap(self, encrypted_value: str) -> str:
        """Re-encrypt DEK with latest Vault key version"""
        import base64

        parts = encrypted_value.split(":")
        if parts[0] != "vault" or len(parts) < 5:
            raise ValueError("Not a Vault-encrypted value")

        # Extract and rewrap the Vault ciphertext
        vault_ciphertext = ":".join(parts[2:-2])
        new_vault_ciphertext = await self.vault.rewrap_dek(vault_ciphertext)

        # Reconstruct with new ciphertext
        parts_new = [parts[0], parts[1], new_vault_ciphertext, parts[-2], parts[-1]]
        return ":".join(parts_new)

    async def close(self):
        """Clean up resources"""
        await self.vault.close()


# Global singleton
_vault_fle: Optional[VaultFieldLevelEncryption] = None


async def get_vault_fle() -> VaultFieldLevelEncryption:
    """Get or create Vault FLE instance"""
    global _vault_fle
    if _vault_fle is None:
        _vault_fle = VaultFieldLevelEncryption()
    return _vault_fle
