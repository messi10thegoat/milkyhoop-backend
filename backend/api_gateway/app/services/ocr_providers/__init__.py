"""
OCR Providers Package
=====================
Modular OCR provider abstraction. Pipeline code uses get_ocr_provider()
and never knows which provider is active.
"""
import os
from .base import OCRProvider, OCRProviderResult

def get_ocr_provider() -> OCRProvider:
    """Factory function. Provider selected via OCR_PROVIDER env var."""
    provider = os.getenv("OCR_PROVIDER", "openai")
    
    if provider == "openai":
        from .openai_provider import OpenAIOCRProvider
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY not set")
        return OpenAIOCRProvider(api_key=api_key)
    else:
        raise ValueError(f"Unknown OCR provider: {provider}. Supported: openai")

__all__ = ["OCRProvider", "OCRProviderResult", "get_ocr_provider"]
