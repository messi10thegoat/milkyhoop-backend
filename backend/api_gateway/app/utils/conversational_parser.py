"""
Conversational Parser - REGEX-based parsing for guided input
NO LLM required - pure pattern matching for structured input
"""
import re
from typing import Dict, Any

# Wholesales units trigger "isi per unit" field
WHOLESALE_UNITS = [
    "karton", "dus", "box", "slop", "bal", "koli", "pack",
    "lusin", "gross", "rim", "roll", "pallet", "sak"
]

RETAIL_UNITS = [
    "pcs", "unit", "buah", "biji", "butir", "lembar", "batang",
    "bungkus", "sachet", "botol", "kaleng", "cup", "porsi",
    "kg", "gram", "liter", "ml"
]

# Regex patterns for parsing conversational input
PATTERNS = {
    'keyword': r'^(Kulakan|kulakan|Beli|beli|Belanja|belanja)\s+',
    'product': r'(?:Kulakan|kulakan|Beli|beli|Belanja|belanja)\s+(.+?)\s+(?:sejumlah|jumlah|\d+)',
    'qty_unit': r'(?:sejumlah|jumlah)?\s*(\d+)\s+(\w+)',
    'price': r'harga\s+(\d+)(?:rb|ribu|000)?(?:\s+per\s+\w+)?',
    'isi': r'isi\s+(\d+)\s+(?:per\s+)?(\w+)',
    'diskon_pct': r'(?:diskon|discount|potongan|pot)\s+(\d+)%',
    'diskon_nom': r'(?:diskon|discount|potongan|pot)\s+(\d+)(?:rb|ribu|000)?(?!\s*%)',
    'ppn': r'\b(ppn|pajak|tax)\b',
    'payment': r'bayar\s+(\w+)',
    'supplier': r'dari\s+(.+?)(?:\s+bayar|\s*$)'
}


def parse_conversational_input(text: str) -> Dict[str, Any]:
    """
    Parse conversational transaction input using REGEX (NO LLM).

    Example input:
    "Kulakan Indomie Goreng 5 karton harga 110rb per karton isi 40 per pcs diskon 10% ppn bayar tunai dari Indogrosir"

    Returns structured dict with all extracted fields.
    """
    result = {
        "keyword": None,
        "product_name": None,
        "quantity": None,
        "unit": None,
        "price_per_unit": None,
        "isi_per_unit": None,
        "unit_kecil": None,
        "discount_type": None,
        "discount_value": None,
        "include_vat": False,
        "payment_method": None,
        "vendor_name": None,
        "transaction_type": "retail"  # default
    }

    # Extract keyword (Kulakan/Beli/Belanja)
    keyword_match = re.search(PATTERNS['keyword'], text)
    if keyword_match:
        result["keyword"] = keyword_match.group(1).capitalize()

    # Extract product name (between keyword and quantity)
    product_match = re.search(PATTERNS['product'], text)
    if product_match:
        result["product_name"] = product_match.group(1).strip()

    # Extract quantity + unit
    qty_match = re.search(PATTERNS['qty_unit'], text)
    if qty_match:
        result["quantity"] = int(qty_match.group(1))
        result["unit"] = qty_match.group(2).lower()

        # Detect wholesales
        if result["unit"] in WHOLESALE_UNITS:
            result["transaction_type"] = "wholesale"

    # Extract price
    price_match = re.search(PATTERNS['price'], text)
    if price_match:
        price_str = price_match.group(1)
        result["price_per_unit"] = int(price_str)

        # Handle "rb" or "ribu" suffix (multiply by 1000)
        match_text = text[price_match.start():price_match.end()]
        if "rb" in match_text or "ribu" in match_text:
            result["price_per_unit"] *= 1000

    # Extract isi per unit (wholesales only)
    isi_match = re.search(PATTERNS['isi'], text)
    if isi_match:
        result["isi_per_unit"] = int(isi_match.group(1))
        result["unit_kecil"] = isi_match.group(2).lower()

    # Extract discount (percentage)
    diskon_pct_match = re.search(PATTERNS['diskon_pct'], text)
    if diskon_pct_match:
        result["discount_type"] = "percentage"
        result["discount_value"] = float(diskon_pct_match.group(1))

    # Extract discount (nominal) - only if percentage not found
    if not diskon_pct_match:
        diskon_nom_match = re.search(PATTERNS['diskon_nom'], text)
        if diskon_nom_match:
            result["discount_type"] = "nominal"
            discount_val = int(diskon_nom_match.group(1))
            match_text = text[diskon_nom_match.start():diskon_nom_match.end()]
            if "rb" in match_text or "ribu" in match_text:
                discount_val *= 1000
            result["discount_value"] = discount_val

    # Extract PPN
    ppn_match = re.search(PATTERNS['ppn'], text, re.IGNORECASE)
    if ppn_match:
        result["include_vat"] = True

    # Extract payment method
    payment_match = re.search(PATTERNS['payment'], text)
    if payment_match:
        result["payment_method"] = payment_match.group(1).lower()

    # Extract supplier
    supplier_match = re.search(PATTERNS['supplier'], text)
    if supplier_match:
        result["vendor_name"] = supplier_match.group(1).strip()

    # Calculate total if we have quantity and price
    if result["quantity"] and result["price_per_unit"]:
        subtotal = result["quantity"] * result["price_per_unit"]

        # Apply discount
        if result["discount_type"] == "percentage" and result["discount_value"]:
            subtotal = subtotal * (1 - result["discount_value"] / 100)
        elif result["discount_type"] == "nominal" and result["discount_value"]:
            subtotal = subtotal - result["discount_value"]

        # Apply VAT
        if result["include_vat"]:
            subtotal = subtotal * 1.11  # 11% VAT

        result["total"] = int(subtotal)
    else:
        result["total"] = 0

    # Calculate HPP if wholesale
    if result["transaction_type"] == "wholesale" and result["isi_per_unit"]:
        total_pieces = result["quantity"] * result["isi_per_unit"]
        if total_pieces > 0:
            result["hpp_per_piece"] = result["total"] / total_pieces

    return result


def validate_parsed_input(parsed: Dict[str, Any]) -> Dict[str, Any]:
    """
    Validate parsed input and return errors if any.
    """
    errors = []

    if not parsed.get("product_name"):
        errors.append("Product name tidak terdeteksi")
    if not parsed.get("quantity"):
        errors.append("Jumlah tidak terdeteksi")
    if not parsed.get("unit"):
        errors.append("Satuan tidak terdeteksi")
    if not parsed.get("price_per_unit"):
        errors.append("Harga tidak terdeteksi")

    return {
        "is_valid": len(errors) == 0,
        "errors": errors
    }
