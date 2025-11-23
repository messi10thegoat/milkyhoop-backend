"""
Item extraction from transaction text

Extracts items array using regex patterns for various transaction formats.
"""

import re
import logging
from typing import List, Dict, Any

logger = logging.getLogger(__name__)


def extract_items_from_text(text: str) -> List[Dict[str, Any]]:
    """
    Fallback regex-based item extraction when LLM fails.

    Handles patterns like:
    - "jual 10 kopi @20000"
    - "beli 5 buku @15rb"
    - "jual teh 100 gelas @Rp 15.000"
    - "jual 10 kaos @45rb, 5 celana @100rb"

    Args:
        text: Transaction text to parse

    Returns:
        List of item dictionaries with keys:
        - nama_produk: str
        - jumlah: int
        - satuan: str
        - harga_satuan: int
        - subtotal: int

    Example:
        >>> extract_items_from_text("jual 10 kopi @15rb")
        [{'nama_produk': 'kopi', 'jumlah': 10, 'satuan': 'pcs', 'harga_satuan': 15000, 'subtotal': 150000}]
    """
    items = []

    # Pattern: quantity product @price
    # Match: "10 kopi @20000", "5 buku @15rb", "100 kaos @45rb"
    # NEW: Also match "teh 100 gelas @Rp 15.000" with satuan and price with dots
    # IMPORTANT: Pattern with satuan must be checked FIRST (most specific)
    patterns = [
        # Pattern 3: "teh 100 gelas @15.000" (product quantity satuan @price) - CHECK FIRST!
        r'(?:jual|beli|bayar)?\s*([a-zA-Z][a-zA-Z0-9\s]*?)\s+(\d+)\s+([a-zA-Z]+)\s+@\s*(?:rp\s*)?(\d+[\.,]?\d*)\s*(rb|ribu|k|jt|juta)?',
        # Pattern 1: "10 kopi @20000" or "10 kopi @20rb"
        r'(\d+)\s+([a-zA-Z][a-zA-Z0-9\s]*?)\s+@\s*(?:rp\s*)?(\d+[\.,]?\d*)\s*(rb|ribu|k|jt|juta)?',
        # Pattern 2: "kopi 10 @20000" (reversed order)
        r'([a-zA-Z][a-zA-Z0-9\s]*?)\s+(\d+)\s+@\s*(?:rp\s*)?(\d+[\.,]?\d*)\s*(rb|ribu|k|jt|juta)?',
    ]

    for pattern_idx, pattern in enumerate(patterns):
        matches = re.findall(pattern, text.lower())
        for match in matches:
            try:
                # Pattern 0 has 5 groups (product, quantity, satuan, price, unit)
                # Pattern 1 & 2 have 4 groups
                if pattern_idx == 0 and len(match) == 5:
                    # Pattern 3: (product, quantity, satuan, price, unit)
                    product_name, quantity_str, satuan, price_str, unit = match
                    # Strip transaction keywords from product name
                    product_name = re.sub(r'^(jual|beli|bayar)\s+', '', product_name).strip()
                elif pattern_idx == 1 and len(match) == 4:
                    # Pattern 1: (quantity, product, price, unit)
                    quantity_str, product_name, price_str, unit = match
                    satuan = "pcs"
                elif pattern_idx == 2 and len(match) == 4:
                    # Pattern 2: (product, quantity, price, unit)
                    product_name, quantity_str, price_str, unit = match
                    satuan = "pcs"
                else:
                    continue  # Skip mismatched patterns

                # Remove dots/commas from price (15.000 → 15000)
                price_str = price_str.replace('.', '').replace(',', '')

                quantity = float(quantity_str)
                price_num = float(price_str)

                # Convert unit to full amount
                if unit and ('jt' in unit or 'juta' in unit):
                    harga_satuan = int(price_num * 1000000)
                elif unit and ('rb' in unit or 'ribu' in unit or 'k' in unit):
                    harga_satuan = int(price_num * 1000)
                else:
                    # No unit, assume raw number
                    harga_satuan = int(price_num)

                subtotal = int(quantity * harga_satuan)

                items.append({
                    "nama_produk": product_name.strip(),
                    "jumlah": int(quantity),
                    "satuan": satuan.strip() if satuan != "pcs" else "pcs",
                    "harga_satuan": harga_satuan,
                    "subtotal": subtotal
                })
            except (ValueError, TypeError) as e:
                logger.warning(f"Failed to parse match {match}: {e}")
                continue

        # CRITICAL: If pattern matched and items found, STOP checking remaining patterns
        # This prevents overlapping matches (e.g. Pattern 0 matches "teh 100 gelas @15rb",
        # then Pattern 1 also matches "100 gelas @15rb" creating duplicate)
        if items:
            break

    return items