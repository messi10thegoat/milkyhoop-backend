import numpy as np
import cv2
from PIL import Image
import io

def preprocess_image_cv(image_bytes: bytes, use_fast_path: bool = True) -> np.ndarray:
    """
    Preprocess image for optimal barcode detection.

    Two paths:
    - Fast path (use_fast_path=True): Minimal preprocessing for clear barcodes (target <100ms)
    - Slow path (use_fast_path=False): Full preprocessing for damaged barcodes (target <300ms)

    Args:
        image_bytes: Raw image bytes (WEBP/JPEG)
        use_fast_path: If True, skip heavy preprocessing

    Returns: Preprocessed grayscale image (numpy array)
    """
    # Load via PIL (handles WEBP, JPEG, PNG)
    try:
        pil = Image.open(io.BytesIO(image_bytes)).convert('RGB')
    except Exception as e:
        raise ValueError(f"Failed to decode image: {e}")

    img = np.array(pil)

    # Convert to grayscale (always needed)
    gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)

    if use_fast_path:
        # FAST PATH: Basic threshold only (~50-100ms)
        # Good enough for 80% of clear barcodes
        _, thresholded = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        return thresholded

    # SLOW PATH: Full preprocessing pipeline (~200-300ms)
    # For damaged/dirty/low-light barcodes

    # CLAHE (Contrast Limited Adaptive Histogram Equalization)
    # Enhances contrast without amplifying noise
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    gray = clahe.apply(gray)

    # Fast non-local means denoising
    # Removes noise while preserving edges
    gray = cv2.fastNlMeansDenoising(gray, None, h=7, templateWindowSize=7, searchWindowSize=21)

    # Adaptive threshold
    # Better for varying lighting conditions
    thresholded = cv2.adaptiveThreshold(
        gray,
        255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY,
        blockSize=11,
        C=2
    )

    # Morphological closing (fill small gaps in bars)
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
    closed = cv2.morphologyEx(thresholded, cv2.MORPH_CLOSE, kernel)

    return closed
