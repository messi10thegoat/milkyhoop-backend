"""
PDF Bank Statement Parser

Extracts tables from digitally-generated PDF bank statements using pdfplumber.
Returns raw rows in the same format as CSV parser output for downstream processing.

NOTE: This handles digitally-generated PDFs only (not scanned/image PDFs).
For scanned PDFs, OCR integration would be needed (future phase).
"""

import logging
from typing import List, Dict, Optional, Tuple

logger = logging.getLogger("pdf_parser")


def extract_tables_from_pdf(content: bytes) -> Tuple[List[List[str]], Optional[List[str]]]:
    """
    Extract table data from a PDF bank statement.

    Args:
        content: Raw PDF file bytes

    Returns:
        Tuple of (rows, header_row):
        - rows: List of rows, each row is a list of cell strings
        - header_row: Detected header row (or None if not detected)
    """
    import pdfplumber
    import io

    all_rows: List[List[str]] = []
    header_row: Optional[List[str]] = None

    try:
        with pdfplumber.open(io.BytesIO(content)) as pdf:
            for page_num, page in enumerate(pdf.pages):
                tables = page.extract_tables()

                if not tables:
                    # Try extracting text as fallback (some statements don't have proper tables)
                    text = page.extract_text()
                    if text:
                        logger.info(
                            f"[PDFParser] Page {page_num + 1}: No tables found, "
                            f"extracted {len(text)} chars of text"
                        )
                    continue

                for table in tables:
                    if not table:
                        continue

                    for row_idx, row in enumerate(table):
                        if not row:
                            continue

                        # Clean cells: strip whitespace, replace None with empty string
                        cleaned = [
                            (cell.strip() if cell else "")
                            for cell in row
                        ]

                        # Skip completely empty rows
                        if not any(cleaned):
                            continue

                        # Detect header row (first row with date-like or common header keywords)
                        if header_row is None and _looks_like_header(cleaned):
                            header_row = cleaned
                            continue

                        all_rows.append(cleaned)

        logger.info(
            f"[PDFParser] Extracted {len(all_rows)} rows, "
            f"header detected: {header_row is not None}"
        )

    except Exception as e:
        logger.error(f"[PDFParser] Extraction failed: {e}")
        raise ValueError(f"Gagal membaca PDF: {str(e)}")

    return all_rows, header_row


def _looks_like_header(row: List[str]) -> bool:
    """Heuristic: does this row look like a table header?"""
    header_keywords = {
        # Indonesian
        "tanggal", "tgl", "keterangan", "deskripsi", "uraian",
        "debit", "kredit", "credit", "saldo", "jumlah", "nominal",
        "mutasi", "referensi", "ref", "no",
        # English
        "date", "description", "amount", "balance", "reference",
        "debit", "credit", "type", "transaction",
    }

    row_lower = " ".join(r.lower() for r in row if r)

    # Count how many header keywords appear
    matches = sum(1 for kw in header_keywords if kw in row_lower)

    # If at least 2 header keywords match, likely a header
    return matches >= 2


def pdf_to_dataframe(content: bytes):
    """
    Convert PDF bank statement to pandas DataFrame.
    Returns DataFrame compatible with the existing CSV import pipeline.
    """
    import pandas as pd

    rows, header = extract_tables_from_pdf(content)

    if not rows:
        raise ValueError("Tidak ada data tabel yang ditemukan di PDF.")

    if header:
        df = pd.DataFrame(rows, columns=header)
    else:
        # No header detected — use first row as header or generate column names
        if rows:
            # Check if first row looks like data or header
            if _looks_like_header(rows[0]):
                df = pd.DataFrame(rows[1:], columns=rows[0])
            else:
                # Generate generic column names
                col_count = max(len(r) for r in rows)
                columns = [f"col_{i}" for i in range(col_count)]
                df = pd.DataFrame(rows, columns=columns)
        else:
            raise ValueError("Tidak ada data yang bisa diproses dari PDF.")

    # Clean up: strip whitespace from all string columns
    for col in df.columns:
        if df[col].dtype == object:
            df[col] = df[col].str.strip()

    logger.info(f"[PDFParser] DataFrame created: {len(df)} rows x {len(df.columns)} columns")
    return df
