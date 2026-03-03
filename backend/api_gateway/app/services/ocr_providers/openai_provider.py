"""
OpenAI Vision OCR Provider
==========================
Uses OpenAI GPT-4o / GPT-4o-mini Vision API for document OCR.
"""
import base64
import io
import json
import logging
import os
import time
from decimal import Decimal
from typing import Optional

from openai import AsyncOpenAI

from .base import OCRProvider, OCRProviderResult

logger = logging.getLogger(__name__)


class OpenAIOCRProvider(OCRProvider):
    """
    OpenAI Vision API provider.
    Tier 2: gpt-4o-mini (cheap, fast)
    Tier 3: gpt-4o (expensive, accurate)
    """

    TIER_MODELS = {
        2: "gpt-4o-mini",
        3: "gpt-4o",
    }

    def __init__(self, api_key: str):
        self.client = AsyncOpenAI(api_key=api_key)

    def get_model_for_tier(self, tier: int) -> str:
        return self.TIER_MODELS.get(tier, "gpt-4o-mini")

    async def extract(
        self,
        file_path: str,
        mime_type: str,
        tier: int,
        prompt: str,
    ) -> OCRProviderResult:
        model = self.get_model_for_tier(tier)
        
        # Convert file to base64 image
        image_b64, image_mime = await self._prepare_image(file_path, mime_type)
        
        # Build message with vision content
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:{image_mime};base64,{image_b64}",
                            "detail": "high",
                        },
                    },
                ],
            }
        ]

        start_ms = time.monotonic_ns() // 1_000_000
        
        try:
            response = await self.client.chat.completions.create(
                model=model,
                messages=messages,
                max_tokens=4096,
                temperature=0.0,
                response_format={"type": "json_object"},
            )
        except Exception as e:
            logger.error(f"[OCR] OpenAI API call failed: {e}")
            raise

        end_ms = time.monotonic_ns() // 1_000_000
        latency = end_ms - start_ms

        # Parse response
        content = response.choices[0].message.content or "{}"
        try:
            raw_json = json.loads(content)
        except json.JSONDecodeError:
            logger.warning(f"[OCR] Invalid JSON from {model}: {content[:200]}")
            raw_json = {"error": "invalid_json", "raw": content[:500]}

        # Extract confidence
        confidence_str = raw_json.get("confidence", "0.5")
        try:
            confidence = Decimal(str(confidence_str))
        except Exception:
            confidence = Decimal("0.5")

        tokens_used = 0
        if response.usage:
            tokens_used = response.usage.total_tokens

        logger.info(
            f"[OCR] {model} completed in {latency}ms, "
            f"{tokens_used} tokens, confidence={confidence}"
        )

        return OCRProviderResult(
            raw_json=raw_json,
            confidence=confidence,
            model_used=model,
            tokens_used=tokens_used,
            latency_ms=latency,
        )

    async def _prepare_image(
        self, file_path: str, mime_type: str
    ) -> tuple[str, str]:
        """
        Convert file to base64 image suitable for Vision API.
        - Images: read directly as base64
        - PDFs: convert first page to PNG using pymupdf
        """
        if mime_type == "application/pdf":
            return self._pdf_to_base64(file_path)
        
        # Image files: read directly
        with open(file_path, "rb") as f:
            data = f.read()
        
        # For HEIC/HEIF, convert to JPEG using Pillow
        if mime_type in ("image/heic", "image/heif"):
            try:
                from PIL import Image
                img = Image.open(io.BytesIO(data))
                buf = io.BytesIO()
                img.save(buf, format="JPEG", quality=95)
                data = buf.getvalue()
                mime_type = "image/jpeg"
            except Exception as e:
                logger.warning(f"[OCR] HEIC conversion failed, sending raw: {e}")
        
        return base64.b64encode(data).decode("utf-8"), mime_type

    def _pdf_to_base64(self, file_path: str) -> tuple[str, str]:
        """Convert first page of PDF to PNG base64."""
        try:
            import fitz  # pymupdf
            doc = fitz.open(file_path)
            page = doc[0]  # First page only
            # Render at 2x for better OCR quality
            mat = fitz.Matrix(2.0, 2.0)
            pix = page.get_pixmap(matrix=mat)
            png_data = pix.tobytes("png")
            doc.close()
            return base64.b64encode(png_data).decode("utf-8"), "image/png"
        except Exception as e:
            logger.error(f"[OCR] PDF conversion failed: {e}")
            raise ValueError(f"Cannot convert PDF to image: {e}")
