"""
Key Rotation Service
====================
Handles scheduled key rotation and batch re-encryption of data.

Compliance Requirements:
- PCI-DSS 3.6.4: Crypto key rotation at least annually
- UU PDP: Data protection including key management

Key Rotation Process:
1. Generate new KEK (or rotate in Vault)
2. Batch re-encrypt all DEKs with new KEK
3. Verify all data can be decrypted
4. Archive old key for recovery (time-limited)
5. Remove old key after verification period
"""
import os
import asyncio
import logging
from typing import List, Dict, Any, Optional, Callable, Awaitable
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass
from enum import Enum

from .fle_service import get_fle, FieldLevelEncryption, is_encrypted

logger = logging.getLogger(__name__)


class RotationStatus(str, Enum):
    """Key rotation job status"""
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    VERIFYING = "verifying"
    COMPLETED = "completed"
    FAILED = "failed"
    ROLLED_BACK = "rolled_back"


@dataclass
class RotationJob:
    """Represents a key rotation job"""
    job_id: str
    old_key_id: str
    new_key_id: str
    status: RotationStatus
    started_at: datetime
    completed_at: Optional[datetime] = None
    total_records: int = 0
    processed_records: int = 0
    failed_records: int = 0
    error_message: Optional[str] = None


@dataclass
class FieldLocation:
    """Describes where an encrypted field is stored"""
    table_name: str
    field_name: str
    id_column: str = "id"
    batch_size: int = 100


class KeyRotationService:
    """
    Manages key rotation and batch re-encryption.

    Usage:
        service = KeyRotationService(db_pool)
        await service.rotate_key("new-key-2024")
        await service.reencrypt_all_fields()
    """

    # Fields that need re-encryption during rotation
    ENCRYPTED_FIELDS: List[FieldLocation] = [
        # User PII
        FieldLocation("User", "email"),
        FieldLocation("User", "name"),
        FieldLocation("User", "fullname"),
        # User Profile
        FieldLocation("UserProfile", "phoneNumber"),
        FieldLocation("UserProfile", "digitalSignature"),
        # Business Info
        FieldLocation("UserBusiness", "taxId", id_column="businessId"),
        FieldLocation("UserBusiness", "businessLicense", id_column="businessId"),
        # Transaction PII
        FieldLocation("transaksi_harian", "nama_pihak"),
        FieldLocation("transaksi_harian", "kontak_pihak"),
        # Supplier
        FieldLocation("suppliers", "kontak"),
        # Orders
        FieldLocation("Order", "customer_name"),
    ]

    def __init__(
        self,
        db_pool,  # asyncpg pool
        fle: Optional[FieldLevelEncryption] = None
    ):
        self.db_pool = db_pool
        self.fle = fle or get_fle()
        self._current_job: Optional[RotationJob] = None

    async def rotate_key(self, new_key_id: str, new_key: bytes) -> RotationJob:
        """
        Add a new key and set it as active.

        Args:
            new_key_id: Identifier for the new key
            new_key: The new 256-bit key

        Returns:
            RotationJob tracking the rotation progress
        """
        import uuid

        old_key_id = self.fle.key_store.active_key_id

        # Create job
        job = RotationJob(
            job_id=str(uuid.uuid4()),
            old_key_id=old_key_id or "none",
            new_key_id=new_key_id,
            status=RotationStatus.PENDING,
            started_at=datetime.now(timezone.utc)
        )

        self._current_job = job

        try:
            # Add new key (but don't activate yet)
            self.fle.key_store.add_key(new_key_id, new_key, set_active=False)

            logger.info(f"Key rotation started: {job.job_id}")
            logger.info(f"Old key: {old_key_id}, New key: {new_key_id}")

            # Count total records
            job.total_records = await self._count_encrypted_records()
            job.status = RotationStatus.IN_PROGRESS

            # Re-encrypt all fields
            await self._reencrypt_all_fields(job, new_key_id)

            # Verify
            job.status = RotationStatus.VERIFYING
            verification_ok = await self._verify_rotation(new_key_id)

            if verification_ok:
                # Activate new key
                self.fle.key_store._active_key_id = new_key_id
                job.status = RotationStatus.COMPLETED
                job.completed_at = datetime.now(timezone.utc)
                logger.info(f"Key rotation completed: {job.job_id}")
            else:
                raise Exception("Verification failed after re-encryption")

        except Exception as e:
            job.status = RotationStatus.FAILED
            job.error_message = str(e)
            logger.error(f"Key rotation failed: {e}")
            await self._rollback(job)

        return job

    async def _count_encrypted_records(self) -> int:
        """Count total encrypted records across all tables"""
        total = 0

        async with self.db_pool.acquire() as conn:
            for field_loc in self.ENCRYPTED_FIELDS:
                try:
                    # Check if column exists and count non-null encrypted values
                    query = f"""
                        SELECT COUNT(*) FROM "{field_loc.table_name}"
                        WHERE "{field_loc.field_name}" IS NOT NULL
                        AND "{field_loc.field_name}" LIKE 'v1:%'
                    """
                    result = await conn.fetchval(query)
                    total += result or 0
                except Exception as e:
                    logger.warning(f"Could not count {field_loc.table_name}.{field_loc.field_name}: {e}")

        return total

    async def _reencrypt_all_fields(self, job: RotationJob, new_key_id: str):
        """Re-encrypt all encrypted fields with new key"""

        for field_loc in self.ENCRYPTED_FIELDS:
            await self._reencrypt_field(field_loc, job, new_key_id)

    async def _reencrypt_field(
        self,
        field_loc: FieldLocation,
        job: RotationJob,
        new_key_id: str
    ):
        """Re-encrypt a single field in batches"""

        async with self.db_pool.acquire() as conn:
            offset = 0

            while True:
                # Fetch batch
                query = f"""
                    SELECT "{field_loc.id_column}", "{field_loc.field_name}"
                    FROM "{field_loc.table_name}"
                    WHERE "{field_loc.field_name}" IS NOT NULL
                    AND "{field_loc.field_name}" LIKE 'v1:%'
                    ORDER BY "{field_loc.id_column}"
                    LIMIT {field_loc.batch_size} OFFSET {offset}
                """

                try:
                    rows = await conn.fetch(query)
                except Exception as e:
                    logger.warning(f"Could not query {field_loc.table_name}: {e}")
                    break

                if not rows:
                    break

                # Process batch
                for row in rows:
                    record_id = row[field_loc.id_column]
                    encrypted_value = row[field_loc.field_name]

                    try:
                        # Re-encrypt with new key
                        # First decrypt with old key
                        aad = f"{field_loc.table_name}.{field_loc.field_name}"
                        plaintext = self.fle.decrypt(encrypted_value, aad)

                        # Then encrypt with new key
                        # Temporarily set new key as active
                        old_active = self.fle.key_store._active_key_id
                        self.fle.key_store._active_key_id = new_key_id

                        new_encrypted = self.fle.encrypt(plaintext, aad)

                        # Restore old active key
                        self.fle.key_store._active_key_id = old_active

                        # Update record
                        update_query = f"""
                            UPDATE "{field_loc.table_name}"
                            SET "{field_loc.field_name}" = $1
                            WHERE "{field_loc.id_column}" = $2
                        """
                        await conn.execute(update_query, new_encrypted, record_id)

                        job.processed_records += 1

                    except Exception as e:
                        logger.error(f"Failed to re-encrypt {field_loc.table_name}.{record_id}: {e}")
                        job.failed_records += 1

                offset += field_loc.batch_size

                # Progress logging
                if job.processed_records % 1000 == 0:
                    progress = (job.processed_records / job.total_records * 100) if job.total_records > 0 else 0
                    logger.info(f"Key rotation progress: {progress:.1f}% ({job.processed_records}/{job.total_records})")

    async def _verify_rotation(self, new_key_id: str, sample_size: int = 100) -> bool:
        """Verify that re-encrypted data can be decrypted with new key"""

        # Temporarily set new key as active
        old_active = self.fle.key_store._active_key_id
        self.fle.key_store._active_key_id = new_key_id

        verified = 0
        failed = 0

        try:
            async with self.db_pool.acquire() as conn:
                for field_loc in self.ENCRYPTED_FIELDS:
                    query = f"""
                        SELECT "{field_loc.id_column}", "{field_loc.field_name}"
                        FROM "{field_loc.table_name}"
                        WHERE "{field_loc.field_name}" IS NOT NULL
                        AND "{field_loc.field_name}" LIKE 'v1:%'
                        LIMIT {sample_size}
                    """

                    try:
                        rows = await conn.fetch(query)
                    except Exception:
                        continue

                    for row in rows:
                        try:
                            aad = f"{field_loc.table_name}.{field_loc.field_name}"
                            self.fle.decrypt(row[field_loc.field_name], aad)
                            verified += 1
                        except Exception as e:
                            logger.error(f"Verification failed for {field_loc.table_name}.{row[field_loc.id_column]}: {e}")
                            failed += 1

        finally:
            # Restore old active key
            self.fle.key_store._active_key_id = old_active

        logger.info(f"Verification: {verified} passed, {failed} failed")
        return failed == 0

    async def _rollback(self, job: RotationJob):
        """Rollback failed rotation"""
        logger.warning(f"Rolling back key rotation: {job.job_id}")

        # Remove new key from key store
        if job.new_key_id in self.fle.key_store._keys:
            del self.fle.key_store._keys[job.new_key_id]

        job.status = RotationStatus.ROLLED_BACK
        logger.info(f"Rollback completed for job: {job.job_id}")

    def get_current_job(self) -> Optional[RotationJob]:
        """Get the current rotation job status"""
        return self._current_job


# ==============================================
# SCHEDULED KEY ROTATION
# ==============================================

async def schedule_key_rotation(
    db_pool,
    rotation_interval_days: int = 90,
    key_generator: Optional[Callable[[], bytes]] = None
):
    """
    Schedule automatic key rotation.

    Args:
        db_pool: Database connection pool
        rotation_interval_days: Days between rotations (default: 90 for PCI-DSS)
        key_generator: Optional custom key generator
    """
    import secrets

    service = KeyRotationService(db_pool)

    def generate_key() -> bytes:
        if key_generator:
            return key_generator()
        return secrets.token_bytes(32)

    while True:
        try:
            # Wait for interval
            await asyncio.sleep(rotation_interval_days * 24 * 60 * 60)

            # Generate new key
            new_key_id = f"key-{datetime.now(timezone.utc).strftime('%Y%m%d')}"
            new_key = generate_key()

            logger.info(f"Starting scheduled key rotation: {new_key_id}")

            # Perform rotation
            job = await service.rotate_key(new_key_id, new_key)

            if job.status == RotationStatus.COMPLETED:
                logger.info(f"Scheduled key rotation completed: {new_key_id}")
            else:
                logger.error(f"Scheduled key rotation failed: {job.error_message}")

        except asyncio.CancelledError:
            logger.info("Key rotation scheduler cancelled")
            break
        except Exception as e:
            logger.error(f"Key rotation error: {e}")
            # Wait before retry
            await asyncio.sleep(3600)
