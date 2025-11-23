"""
Employee Name and Period Extractor
Extracts employee names and salary period (bulan) from text
"""

import re
from datetime import datetime
from typing import List, Optional


def extract_employee_names(text: str) -> Optional[str]:
    """
    Extract employee names from text

    Patterns supported:
    1. "Anna Rp 3juta, Bambang Rp 3 juta, Inggrid Rp 3 JUTA"
    2. "untuk Anna, Bambang, dan Inggrid"
    3. "Anna, Bambang, Inggrid"
    4. "gaji Anna" or "gaji untuk Anna"

    Returns:
        Comma-separated employee names (e.g., "Anna, Bambang, Inggrid")
        or None if no names found
    """
    names = []
    skip_words = {
        "Rp", "Juta", "JUTA", "Million", "Bulan",
        "November", "Desember", "Januari", "Februari", "Maret", "April",
        "Mei", "Juni", "Juli", "Agustus", "September", "Oktober",
        "Bayar", "Gaji", "Untuk", "Dan", "Dengan"
    }

    # Pattern 1: Extract names before "Rp" (format: "Name Rp amount")
    name_before_rp = re.findall(r'([A-Z][a-z]+)\s+Rp\s*\d', text)
    for name in name_before_rp:
        if name not in skip_words and len(name) > 2:
            names.append(name)

    # Pattern 2: Extract names after colon (format: ": Name1 Rp, Name2 Rp")
    if ':' in text:
        after_colon = text.split(':', 1)[1]
        name_after_colon = re.findall(r'([A-Z][a-z]+)\s+Rp\s*\d', after_colon)
        for name in name_after_colon:
            if name not in skip_words and len(name) > 2:
                names.append(name)

    # Pattern 3: Extract from "untuk Name1, Name2, dan Name3"
    untuk_match = re.search(
        r'(?:untuk|gaji\s+untuk)\s+([A-Z][a-z]+(?:\s*,\s*(?:dan\s+)?[A-Z][a-z]+)*)',
        text
    )
    if untuk_match:
        names_str = untuk_match.group(1)
        # Split by comma and "dan"
        extracted_names = re.split(r',\s*(?:dan\s+)?', names_str)
        for name in extracted_names:
            name = name.strip()
            if name and name[0].isupper() and name not in skip_words and len(name) > 2:
                names.append(name)

    # Pattern 4: Extract standalone capitalized words separated by comma
    if not names:
        comma_separated = re.findall(
            r'([A-Z][a-z]+)(?:\s*,\s*([A-Z][a-z]+))*(?:\s*,\s*(?:dan\s+)?([A-Z][a-z]+))?',
            text
        )
        for match in comma_separated:
            for name in match:
                if name and name not in skip_words and len(name) > 2:
                    names.append(name)

    # Remove duplicates while preserving order
    names = list(dict.fromkeys(names))

    if names:
        return ", ".join(names)
    return None


def extract_periode_gaji(text: str) -> Optional[str]:
    """
    Extract salary period (month) from text

    Patterns supported:
    1. "bulan November"
    2. "November" (standalone month name)
    3. "bulan ini" (current month)

    Returns:
        Month name in Indonesian (e.g., "November")
        or None if not found
    """
    text_lower = text.lower()

    bulan_map = {
        "januari": "Januari", "februari": "Februari", "maret": "Maret", "april": "April",
        "mei": "Mei", "juni": "Juni", "juli": "Juli", "agustus": "Agustus",
        "september": "September", "oktober": "Oktober", "november": "November", "desember": "Desember"
    }

    # Pattern: "bulan November" or just "November"
    periode_patterns = [
        r'bulan\s+(november|desember|januari|februari|maret|april|mei|juni|juli|agustus|september|oktober)',
        r'(november|desember|januari|februari|maret|april|mei|juni|juli|agustus|september|oktober)',
    ]

    for pattern in periode_patterns:
        match = re.search(pattern, text_lower)
        if match:
            bulan = match.group(1)
            return bulan_map.get(bulan, bulan.capitalize())

    # If not found, check for "bulan ini"
    if "bulan ini" in text_lower:
        bulan_map_num = {
            1: "Januari", 2: "Februari", 3: "Maret", 4: "April",
            5: "Mei", 6: "Juni", 7: "Juli", 8: "Agustus",
            9: "September", 10: "Oktober", 11: "November", 12: "Desember"
        }
        current_month = datetime.now().month
        return bulan_map_num.get(current_month, "bulan ini")

    return None