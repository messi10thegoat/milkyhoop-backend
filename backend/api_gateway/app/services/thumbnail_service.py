"""
Thumbnail Service - Image Processing

Generates thumbnails and extracts dimensions from images using Pillow.
"""

import io
import logging
from typing import Optional, Tuple
from dataclasses import dataclass

from PIL import Image, ExifTags

logger = logging.getLogger(__name__)


@dataclass
class ImageInfo:
    """Information extracted from an image."""
    width: int
    height: int
    format: str
    mode: str


@dataclass
class ThumbnailResult:
    """Result of thumbnail generation."""
    content: bytes
    width: int
    height: int
    original_width: int
    original_height: int
    content_type: str = "image/jpeg"


class ThumbnailService:
    """
    Image processing service for thumbnail generation.

    Usage:
        thumbnail_svc = ThumbnailService()
        result = thumbnail_svc.generate_thumbnail(image_bytes)
        dimensions = thumbnail_svc.get_image_dimensions(image_bytes)
    """

    # Default thumbnail size (max width/height, maintains aspect ratio)
    THUMBNAIL_SIZE = (200, 200)

    # JPEG quality for thumbnails
    THUMBNAIL_QUALITY = 85

    # Supported image types
    SUPPORTED_TYPES = {
        "image/jpeg",
        "image/png",
        "image/webp",
        "image/gif",
    }

    # HEIC/HEIF support requires pillow-heif
    HEIC_TYPES = {
        "image/heic",
        "image/heif",
    }

    def __init__(self, thumbnail_size: Tuple[int, int] = None):
        """Initialize thumbnail service."""
        self.thumbnail_size = thumbnail_size or self.THUMBNAIL_SIZE
        self._heic_available = self._check_heic_support()

    def _check_heic_support(self) -> bool:
        """Check if HEIC/HEIF support is available."""
        try:
            import pillow_heif
            pillow_heif.register_heif_opener()
            return True
        except ImportError:
            logger.warning(
                "pillow-heif not installed. HEIC/HEIF support disabled. "
                "Install with: pip install pillow-heif"
            )
            return False

    def is_supported(self, content_type: str) -> bool:
        """Check if content type is supported for processing."""
        if content_type in self.SUPPORTED_TYPES:
            return True
        if content_type in self.HEIC_TYPES:
            return self._heic_available
        return False

    def _fix_orientation(self, image: Image.Image) -> Image.Image:
        """
        Fix image orientation based on EXIF data.

        Mobile photos often have rotation in EXIF rather than actual pixels.
        """
        try:
            # Get EXIF orientation tag
            for orientation in ExifTags.TAGS.keys():
                if ExifTags.TAGS[orientation] == "Orientation":
                    break

            exif = image._getexif()
            if exif is None:
                return image

            orientation_value = exif.get(orientation)
            if orientation_value is None:
                return image

            # Apply rotation/flip based on EXIF orientation
            if orientation_value == 2:
                image = image.transpose(Image.FLIP_LEFT_RIGHT)
            elif orientation_value == 3:
                image = image.rotate(180)
            elif orientation_value == 4:
                image = image.transpose(Image.FLIP_TOP_BOTTOM)
            elif orientation_value == 5:
                image = image.rotate(-90, expand=True).transpose(Image.FLIP_LEFT_RIGHT)
            elif orientation_value == 6:
                image = image.rotate(-90, expand=True)
            elif orientation_value == 7:
                image = image.rotate(90, expand=True).transpose(Image.FLIP_LEFT_RIGHT)
            elif orientation_value == 8:
                image = image.rotate(90, expand=True)

        except (AttributeError, KeyError, IndexError) as e:
            logger.debug(f"Could not read EXIF orientation: {e}")

        return image

    def get_image_dimensions(
        self,
        content: bytes,
    ) -> Optional[Tuple[int, int]]:
        """
        Get width and height of an image.

        Args:
            content: Image file content as bytes

        Returns:
            Tuple of (width, height) or None if not an image
        """
        try:
            with Image.open(io.BytesIO(content)) as img:
                img = self._fix_orientation(img)
                return img.size
        except Exception as e:
            logger.debug(f"Could not get image dimensions: {e}")
            return None

    def get_image_info(
        self,
        content: bytes,
    ) -> Optional[ImageInfo]:
        """
        Get detailed information about an image.

        Args:
            content: Image file content as bytes

        Returns:
            ImageInfo object or None if not an image
        """
        try:
            with Image.open(io.BytesIO(content)) as img:
                img = self._fix_orientation(img)
                return ImageInfo(
                    width=img.width,
                    height=img.height,
                    format=img.format or "unknown",
                    mode=img.mode,
                )
        except Exception as e:
            logger.debug(f"Could not get image info: {e}")
            return None

    def generate_thumbnail(
        self,
        content: bytes,
        max_size: Tuple[int, int] = None,
        quality: int = None,
    ) -> Optional[ThumbnailResult]:
        """
        Generate a thumbnail from an image.

        Args:
            content: Original image content as bytes
            max_size: Maximum thumbnail dimensions (width, height)
            quality: JPEG quality (1-100)

        Returns:
            ThumbnailResult with thumbnail bytes and dimensions,
            or None if generation fails
        """
        max_size = max_size or self.thumbnail_size
        quality = quality or self.THUMBNAIL_QUALITY

        try:
            with Image.open(io.BytesIO(content)) as img:
                # Fix orientation first
                img = self._fix_orientation(img)
                original_width, original_height = img.size

                # Convert to RGB if necessary (for JPEG output)
                if img.mode in ("RGBA", "LA", "P"):
                    # Create white background for transparent images
                    background = Image.new("RGB", img.size, (255, 255, 255))
                    if img.mode == "P":
                        img = img.convert("RGBA")
                    background.paste(img, mask=img.split()[-1] if img.mode == "RGBA" else None)
                    img = background
                elif img.mode != "RGB":
                    img = img.convert("RGB")

                # Generate thumbnail (maintains aspect ratio)
                img.thumbnail(max_size, Image.Resampling.LANCZOS)

                # Save to bytes
                output = io.BytesIO()
                img.save(output, format="JPEG", quality=quality, optimize=True)
                output.seek(0)

                return ThumbnailResult(
                    content=output.getvalue(),
                    width=img.width,
                    height=img.height,
                    original_width=original_width,
                    original_height=original_height,
                    content_type="image/jpeg",
                )

        except Exception as e:
            logger.error(f"Failed to generate thumbnail: {e}")
            return None

    def resize_image(
        self,
        content: bytes,
        max_dimension: int = 1920,
        quality: int = 85,
    ) -> Optional[bytes]:
        """
        Resize image to fit within max dimension while maintaining aspect ratio.

        Useful for optimizing large images before storage.

        Args:
            content: Original image content
            max_dimension: Maximum width or height
            quality: JPEG quality

        Returns:
            Resized image bytes or None if processing fails
        """
        try:
            with Image.open(io.BytesIO(content)) as img:
                img = self._fix_orientation(img)

                # Only resize if larger than max dimension
                if max(img.size) <= max_dimension:
                    return content

                # Calculate new size
                ratio = max_dimension / max(img.size)
                new_size = (int(img.width * ratio), int(img.height * ratio))

                # Convert mode if necessary
                if img.mode in ("RGBA", "LA", "P"):
                    background = Image.new("RGB", img.size, (255, 255, 255))
                    if img.mode == "P":
                        img = img.convert("RGBA")
                    background.paste(img, mask=img.split()[-1] if img.mode == "RGBA" else None)
                    img = background
                elif img.mode != "RGB":
                    img = img.convert("RGB")

                # Resize
                img = img.resize(new_size, Image.Resampling.LANCZOS)

                # Save
                output = io.BytesIO()
                img.save(output, format="JPEG", quality=quality, optimize=True)
                output.seek(0)

                return output.getvalue()

        except Exception as e:
            logger.error(f"Failed to resize image: {e}")
            return None


# Singleton instance
_thumbnail_service: Optional[ThumbnailService] = None


def get_thumbnail_service() -> ThumbnailService:
    """Get or create thumbnail service singleton."""
    global _thumbnail_service
    if _thumbnail_service is None:
        _thumbnail_service = ThumbnailService()
    return _thumbnail_service
