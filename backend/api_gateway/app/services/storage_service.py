"""
Storage Service - MinIO/S3 Compatible Object Storage

Handles file uploads, downloads, and signed URL generation for documents.
Supports both MinIO (self-hosted) and AWS S3.
"""

import os
import uuid
import logging
from datetime import datetime
from typing import Optional, Tuple
from dataclasses import dataclass

import boto3
from botocore.client import Config
from botocore.exceptions import ClientError
from fastapi import UploadFile

logger = logging.getLogger(__name__)


@dataclass
class StorageResult:
    """Result of a storage operation."""
    file_path: str
    file_size: int
    content_type: str
    url: str
    thumbnail_path: Optional[str] = None
    thumbnail_url: Optional[str] = None


@dataclass
class StorageConfig:
    """MinIO/S3 storage configuration."""
    endpoint: str
    access_key: str
    secret_key: str
    bucket: str
    region: str = "us-east-1"
    use_ssl: bool = False
    url_expiry: int = 3600  # 1 hour
    thumbnail_expiry: int = 86400  # 24 hours
    public_endpoint: Optional[str] = None  # External URL for client access

    @classmethod
    def from_env(cls) -> "StorageConfig":
        """Load configuration from environment variables."""
        return cls(
            endpoint=os.getenv("MINIO_ENDPOINT", "minio:9000"),
            access_key=os.getenv("MINIO_ACCESS_KEY", "minioadmin"),
            secret_key=os.getenv("MINIO_SECRET_KEY", "minioadmin"),
            bucket=os.getenv("MINIO_BUCKET", "milkyhoop-documents"),
            region=os.getenv("MINIO_REGION", "us-east-1"),
            use_ssl=os.getenv("MINIO_USE_SSL", "false").lower() == "true",
            url_expiry=int(os.getenv("MINIO_URL_EXPIRY", "3600")),
            thumbnail_expiry=int(os.getenv("MINIO_THUMBNAIL_EXPIRY", "86400")),
            public_endpoint=os.getenv("MINIO_PUBLIC_ENDPOINT"),  # e.g., localhost:9000
        )


class StorageService:
    """
    MinIO/S3 storage abstraction for file operations.

    Usage:
        storage = StorageService()
        result = await storage.upload_file(file, tenant_id, "receipts")
        url = await storage.generate_signed_url(result.file_path)
    """

    # Allowed MIME types for upload
    ALLOWED_IMAGE_TYPES = {
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/webp": ".webp",
        "image/heic": ".heic",
        "image/heif": ".heif",
    }

    ALLOWED_DOCUMENT_TYPES = {
        "application/pdf": ".pdf",
    }

    ALLOWED_TYPES = {**ALLOWED_IMAGE_TYPES, **ALLOWED_DOCUMENT_TYPES}

    MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB

    def __init__(self, config: Optional[StorageConfig] = None):
        """Initialize storage service with configuration."""
        self.config = config or StorageConfig.from_env()
        self._client: Optional[boto3.client] = None
        self._url_client: Optional[boto3.client] = None  # For presigned URLs

    @property
    def client(self) -> boto3.client:
        """Get or create S3 client for internal operations (lazy initialization)."""
        if self._client is None:
            protocol = "https" if self.config.use_ssl else "http"
            endpoint_url = f"{protocol}://{self.config.endpoint}"

            self._client = boto3.client(
                "s3",
                endpoint_url=endpoint_url,
                aws_access_key_id=self.config.access_key,
                aws_secret_access_key=self.config.secret_key,
                region_name=self.config.region,
                config=Config(
                    signature_version="s3v4",
                    s3={"addressing_style": "path"},
                ),
            )
        return self._client

    @property
    def url_client(self) -> boto3.client:
        """Get or create S3 client for presigned URL generation (uses public endpoint)."""
        if self._url_client is None:
            protocol = "https" if self.config.use_ssl else "http"
            # Use public endpoint if configured, otherwise fall back to internal
            endpoint = self.config.public_endpoint or self.config.endpoint
            endpoint_url = f"{protocol}://{endpoint}"

            self._url_client = boto3.client(
                "s3",
                endpoint_url=endpoint_url,
                aws_access_key_id=self.config.access_key,
                aws_secret_access_key=self.config.secret_key,
                region_name=self.config.region,
                config=Config(
                    signature_version="s3v4",
                    s3={"addressing_style": "path"},
                ),
            )
        return self._url_client

    async def ensure_bucket_exists(self) -> bool:
        """Ensure the storage bucket exists, create if not."""
        try:
            self.client.head_bucket(Bucket=self.config.bucket)
            return True
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code")
            if error_code == "404":
                try:
                    self.client.create_bucket(Bucket=self.config.bucket)
                    logger.info(f"Created bucket: {self.config.bucket}")
                    return True
                except ClientError as create_error:
                    logger.error(f"Failed to create bucket: {create_error}")
                    return False
            logger.error(f"Error checking bucket: {e}")
            return False

    def _generate_file_path(
        self,
        tenant_id: str,
        category: str,
        filename: str,
    ) -> str:
        """
        Generate storage path for a file.

        Format: {tenant_id}/{category}/{year}/{month}/{uuid}_{sanitized_filename}
        Example: tenant-abc/receipts/2026/01/a1b2c3d4_struk-pln.jpg
        """
        now = datetime.utcnow()
        file_uuid = uuid.uuid4().hex[:8]

        # Sanitize filename - keep only alphanumeric, dash, underscore, dot
        safe_filename = "".join(
            c if c.isalnum() or c in "-_." else "_"
            for c in filename
        )

        # Limit filename length
        name, ext = os.path.splitext(safe_filename)
        if len(name) > 50:
            name = name[:50]
        safe_filename = f"{name}{ext}"

        return f"{tenant_id}/{category}/{now.year}/{now.month:02d}/{file_uuid}_{safe_filename}"

    def validate_file(
        self,
        content_type: str,
        file_size: int,
    ) -> Tuple[bool, Optional[str]]:
        """
        Validate file type and size.

        Returns:
            Tuple of (is_valid, error_message)
        """
        if content_type not in self.ALLOWED_TYPES:
            allowed = ", ".join(self.ALLOWED_TYPES.keys())
            return False, f"Invalid file type. Allowed: {allowed}"

        if file_size > self.MAX_FILE_SIZE:
            max_mb = self.MAX_FILE_SIZE / (1024 * 1024)
            return False, f"File too large. Maximum size: {max_mb:.0f}MB"

        return True, None

    async def upload_file(
        self,
        file: UploadFile,
        tenant_id: str,
        category: str = "documents",
    ) -> StorageResult:
        """
        Upload a file to storage.

        Args:
            file: FastAPI UploadFile object
            tenant_id: Tenant identifier for path organization
            category: Category for path organization (e.g., "receipts", "invoices")

        Returns:
            StorageResult with file path and signed URL

        Raises:
            ValueError: If file validation fails
            ClientError: If upload fails
        """
        # Read file content
        content = await file.read()
        file_size = len(content)
        content_type = file.content_type or "application/octet-stream"

        # Validate
        is_valid, error = self.validate_file(content_type, file_size)
        if not is_valid:
            raise ValueError(error)

        # Generate path
        file_path = self._generate_file_path(
            tenant_id,
            category,
            file.filename or "unnamed",
        )

        # Ensure bucket exists
        await self.ensure_bucket_exists()

        # Upload to S3/MinIO
        try:
            self.client.put_object(
                Bucket=self.config.bucket,
                Key=file_path,
                Body=content,
                ContentType=content_type,
                Metadata={
                    "tenant_id": tenant_id,
                    "original_filename": file.filename or "unnamed",
                    "category": category,
                },
            )
        except ClientError as e:
            logger.error(f"Failed to upload file: {e}")
            raise

        # Generate signed URL
        url = await self.generate_signed_url(file_path)

        return StorageResult(
            file_path=file_path,
            file_size=file_size,
            content_type=content_type,
            url=url,
        )

    async def upload_bytes(
        self,
        content: bytes,
        file_path: str,
        content_type: str,
        metadata: Optional[dict] = None,
    ) -> str:
        """
        Upload raw bytes to storage (used for thumbnails).

        Args:
            content: File content as bytes
            file_path: Full storage path
            content_type: MIME type
            metadata: Optional metadata dict

        Returns:
            Signed URL for the uploaded file
        """
        await self.ensure_bucket_exists()

        try:
            self.client.put_object(
                Bucket=self.config.bucket,
                Key=file_path,
                Body=content,
                ContentType=content_type,
                Metadata=metadata or {},
            )
        except ClientError as e:
            logger.error(f"Failed to upload bytes: {e}")
            raise

        return await self.generate_signed_url(
            file_path,
            expires_in=self.config.thumbnail_expiry,
        )

    async def generate_signed_url(
        self,
        file_path: str,
        expires_in: Optional[int] = None,
    ) -> str:
        """
        Generate a pre-signed URL for file download.

        Uses url_client (with public endpoint) so signature matches the
        endpoint that clients will access.

        Args:
            file_path: Path to the file in storage
            expires_in: URL expiry time in seconds (default from config)

        Returns:
            Pre-signed URL string (uses public endpoint if configured)
        """
        expiry = expires_in or self.config.url_expiry

        try:
            url = self.url_client.generate_presigned_url(
                "get_object",
                Params={
                    "Bucket": self.config.bucket,
                    "Key": file_path,
                },
                ExpiresIn=expiry,
            )
            return url
        except ClientError as e:
            logger.error(f"Failed to generate signed URL: {e}")
            raise

    async def delete_file(self, file_path: str) -> bool:
        """
        Delete a file from storage.

        Args:
            file_path: Path to the file in storage

        Returns:
            True if deleted, False if error
        """
        try:
            self.client.delete_object(
                Bucket=self.config.bucket,
                Key=file_path,
            )
            return True
        except ClientError as e:
            logger.error(f"Failed to delete file: {e}")
            return False

    async def file_exists(self, file_path: str) -> bool:
        """Check if a file exists in storage."""
        try:
            self.client.head_object(
                Bucket=self.config.bucket,
                Key=file_path,
            )
            return True
        except ClientError:
            return False

    async def get_file_metadata(self, file_path: str) -> Optional[dict]:
        """
        Get metadata for a file in storage.

        Returns:
            Dict with ContentLength, ContentType, LastModified, Metadata
            or None if file doesn't exist
        """
        try:
            response = self.client.head_object(
                Bucket=self.config.bucket,
                Key=file_path,
            )
            return {
                "size": response.get("ContentLength"),
                "content_type": response.get("ContentType"),
                "last_modified": response.get("LastModified"),
                "metadata": response.get("Metadata", {}),
            }
        except ClientError:
            return None

    def is_image(self, content_type: str) -> bool:
        """Check if content type is an image."""
        return content_type in self.ALLOWED_IMAGE_TYPES

    def get_thumbnail_path(self, file_path: str) -> str:
        """Generate thumbnail path from original file path."""
        name, ext = os.path.splitext(file_path)
        return f"{name}_thumb.jpg"


# Singleton instance
_storage_service: Optional[StorageService] = None


def get_storage_service() -> StorageService:
    """Get or create storage service singleton."""
    global _storage_service
    if _storage_service is None:
        _storage_service = StorageService()
    return _storage_service
