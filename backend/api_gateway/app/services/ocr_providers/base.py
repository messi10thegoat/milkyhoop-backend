"""
OCR Provider Base
=================
Abstract base class for all OCR providers.
All providers MUST return the same OCRProviderResult format.
Pipeline code in document_processor.py is provider-agnostic.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from decimal import Decimal
from typing import Optional


@dataclass
class OCRProviderResult:
    """Standard output from all OCR providers."""
    raw_json: dict          # Raw JSON response from provider
    confidence: Decimal     # 0.000 to 1.000
    model_used: str         # "gpt-4o-mini", "gpt-4o", etc.
    tokens_used: int        # For cost tracking
    latency_ms: int         # For performance monitoring


class OCRProvider(ABC):
    """
    Abstract base for OCR providers.
    All providers MUST return OCRProviderResult format.
    Pipeline code does NOT know which provider is active.
    """

    @abstractmethod
    async def extract(
        self,
        file_path: str,
        mime_type: str,
        tier: int,
        prompt: str,
    ) -> OCRProviderResult:
        """Extract structured data from document image/PDF."""
        pass

    @abstractmethod
    def get_model_for_tier(self, tier: int) -> str:
        """Return model name for given tier."""
        pass
