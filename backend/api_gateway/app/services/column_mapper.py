"""
Column Mapper Service

3-tier column detection for bank statement imports:
  Tier 1: Saved mapping template (instant, confidence=1.0)
  Tier 2: Heuristic regex matching (fast, confidence varies)
  Tier 3: LLM fallback (slow, for ambiguous columns)

Ported from frontend useStatementImport.ts regex + expanded Indonesian patterns.
"""

import re
import logging
from typing import Optional
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


# ─── Column Detection Patterns ───────────────────────────────────────────────
# Each pattern maps a regex to a canonical field name.
# Patterns are checked in order; first match wins per column.

COLUMN_PATTERNS: dict[str, list[re.Pattern]] = {
    "date": [
        re.compile(r"tanggal|tgl|date|posting.?date|value.?date|tgl.?transaksi|tgl.?mutasi", re.I),
    ],
    "description": [
        re.compile(r"keterangan|description|desc|uraian|narasi|berita|catatan|memo|remark|particular", re.I),
    ],
    "amount": [
        re.compile(r"^(jumlah|amount|nominal|nilai|mutasi)$", re.I),
        re.compile(r"jumlah|amount|nominal|nilai", re.I),
    ],
    "debit": [
        re.compile(r"debit|debet|keluar|withdrawal|pengeluaran|tarikan|db", re.I),
    ],
    "credit": [
        re.compile(r"kredit|credit|masuk|deposit|setoran|penerimaan|cr", re.I),
    ],
    "reference": [
        re.compile(r"referensi|reference|ref|no\.?\s*ref|nomor.?ref|trx.?id|transaction.?id", re.I),
    ],
    "balance": [
        re.compile(r"saldo|balance|sisa|running", re.I),
    ],
}

# Date format detection patterns
DATE_FORMAT_PATTERNS = [
    (re.compile(r"^\d{4}-\d{2}-\d{2}$"), "YYYY-MM-DD"),
    (re.compile(r"^\d{2}/\d{2}/\d{4}$"), "DD/MM/YYYY"),
    (re.compile(r"^\d{2}-\d{2}-\d{4}$"), "DD-MM-YYYY"),
    (re.compile(r"^\d{2}\.\d{2}\.\d{4}$"), "DD.MM.YYYY"),
    (re.compile(r"^\d{1,2}/\d{1,2}/\d{4}$"), "DD/MM/YYYY"),
    (re.compile(r"^\d{1,2}-\w{3}-\d{4}$"), "DD-MMM-YYYY"),
]


@dataclass
class ColumnMatch:
    """A detected column mapping with confidence."""
    field: str            # canonical field name (date, description, amount, etc.)
    column_name: str      # actual column name in the file
    confidence: float     # 0.0 - 1.0
    source: str = "heuristic"  # heuristic | template | llm


@dataclass
class MappingResult:
    """Complete column mapping result."""
    matches: dict[str, ColumnMatch] = field(default_factory=dict)
    date_format: str = "DD/MM/YYYY"
    decimal_separator: str = ","
    skip_rows: int = 0
    overall_confidence: float = 0.0
    source: str = "heuristic"  # heuristic | template | llm

    def to_import_config(self) -> dict:
        """Convert to ImportConfigCSV-compatible dict for the import endpoint."""
        config: dict = {
            "format": "csv",
            "date_format": self.date_format,
            "decimal_separator": self.decimal_separator,
            "skip_rows": self.skip_rows,
        }
        if "date" in self.matches:
            config["date_column"] = self.matches["date"].column_name
        if "description" in self.matches:
            config["description_column"] = self.matches["description"].column_name
        if "amount" in self.matches:
            config["amount_column"] = self.matches["amount"].column_name
        if "debit" in self.matches:
            config["debit_column"] = self.matches["debit"].column_name
        if "credit" in self.matches:
            config["credit_column"] = self.matches["credit"].column_name
        if "reference" in self.matches:
            config["reference_column"] = self.matches["reference"].column_name
        if "balance" in self.matches:
            config["balance_column"] = self.matches["balance"].column_name
        return config


# ─── Tier 1: Template Lookup ─────────────────────────────────────────────────

async def lookup_mapping_template(
    tenant_id: str,
    column_names: list[str],
    pool,
) -> Optional[MappingResult]:
    """
    Check if we have a saved mapping template for this exact set of columns.
    Returns None if no template found.
    """
    # Normalize column names for comparison
    normalized = "|".join(sorted(c.strip().lower() for c in column_names if c.strip()))

    try:
        row = await pool.fetchrow("""
            SELECT column_mapping, date_format, decimal_separator, skip_rows
            FROM mapping_templates
            WHERE tenant_id = $1
              AND normalized_columns = $2
            ORDER BY use_count DESC, last_used_at DESC
            LIMIT 1
        """, tenant_id, normalized)
    except Exception:
        # Table might not exist yet — graceful fallback
        return None

    if not row:
        return None

    import json
    mapping_data = json.loads(row["column_mapping"]) if isinstance(row["column_mapping"], str) else row["column_mapping"]

    result = MappingResult(
        date_format=row.get("date_format") or "DD/MM/YYYY",
        decimal_separator=row.get("decimal_separator") or ",",
        skip_rows=row.get("skip_rows") or 0,
        overall_confidence=1.0,
        source="template",
    )

    for field_name, col_name in mapping_data.items():
        if col_name and field_name in COLUMN_PATTERNS:
            result.matches[field_name] = ColumnMatch(
                field=field_name,
                column_name=col_name,
                confidence=1.0,
                source="template",
            )

    # Update use_count
    try:
        await pool.execute("""
            UPDATE mapping_templates
            SET use_count = use_count + 1, last_used_at = NOW()
            WHERE tenant_id = $1 AND normalized_columns = $2
        """, tenant_id, normalized)
    except Exception:
        pass  # Non-critical

    return result


async def save_mapping_template(
    tenant_id: str,
    column_names: list[str],
    mapping_result: MappingResult,
    source_entity: str,
    pool,
) -> None:
    """Save a confirmed mapping as a template for future imports."""
    import json
    normalized = "|".join(sorted(c.strip().lower() for c in column_names if c.strip()))
    column_mapping = {
        field: match.column_name
        for field, match in mapping_result.matches.items()
    }

    try:
        await pool.execute("""
            INSERT INTO mapping_templates (
                tenant_id, source_entity, document_type,
                column_mapping, normalized_columns,
                date_format, decimal_separator, skip_rows,
                confidence
            ) VALUES ($1, $2, 'bank_statement', $3, $4, $5, $6, $7, $8)
            ON CONFLICT (tenant_id, normalized_columns)
            DO UPDATE SET
                column_mapping = EXCLUDED.column_mapping,
                date_format = EXCLUDED.date_format,
                decimal_separator = EXCLUDED.decimal_separator,
                skip_rows = EXCLUDED.skip_rows,
                use_count = mapping_templates.use_count + 1,
                last_used_at = NOW(),
                confidence = EXCLUDED.confidence
        """,
            tenant_id, source_entity,
            json.dumps(column_mapping), normalized,
            mapping_result.date_format, mapping_result.decimal_separator,
            mapping_result.skip_rows, mapping_result.overall_confidence,
        )
    except Exception as e:
        logger.warning(f"Failed to save mapping template: {e}")


# ─── Tier 2: Heuristic Detection ─────────────────────────────────────────────

def detect_columns_heuristic(columns: list[str]) -> MappingResult:
    """
    Detect column mapping using regex pattern matching.
    Returns MappingResult with confidence scores.
    """
    result = MappingResult(source="heuristic")
    used_columns: set[str] = set()

    # Try each canonical field
    for field_name, patterns in COLUMN_PATTERNS.items():
        best_match: Optional[ColumnMatch] = None
        best_score = 0.0

        for col in columns:
            if col in used_columns:
                continue
            col_clean = col.strip()
            if not col_clean:
                continue

            for pattern in patterns:
                if pattern.search(col_clean):
                    # Exact match (whole string) gets higher confidence
                    if pattern.fullmatch(col_clean):
                        score = 0.95
                    else:
                        score = 0.85
                    if score > best_score:
                        best_score = score
                        best_match = ColumnMatch(
                            field=field_name,
                            column_name=col,
                            confidence=score,
                            source="heuristic",
                        )
                    break  # First matching pattern wins for this column

        if best_match:
            result.matches[field_name] = best_match
            used_columns.add(best_match.column_name)

    # If we have both debit+credit, remove amount (dual-column mode)
    if "debit" in result.matches and "credit" in result.matches:
        result.matches.pop("amount", None)
    # If we have amount but no debit/credit, that's single-column mode (fine)
    elif "amount" in result.matches:
        result.matches.pop("debit", None)
        result.matches.pop("credit", None)

    # Calculate overall confidence
    required_fields = {"date", "description"}
    amount_fields = {"amount"} if "amount" in result.matches else {"debit", "credit"}
    all_needed = required_fields | amount_fields

    if all_needed.issubset(result.matches.keys()):
        confidences = [result.matches[f].confidence for f in all_needed]
        result.overall_confidence = min(confidences)
    else:
        missing = all_needed - result.matches.keys()
        # Partial detection — low confidence
        result.overall_confidence = max(0.0, 0.5 - 0.2 * len(missing))

    return result


def detect_date_format(sample_values: list[str]) -> str:
    """Detect date format from sample values."""
    for value in sample_values[:10]:
        v = str(value).strip()
        if not v:
            continue
        for pattern, fmt in DATE_FORMAT_PATTERNS:
            if pattern.match(v):
                return fmt
    return "DD/MM/YYYY"  # default


def detect_decimal_separator(sample_values: list[str]) -> str:
    """Detect decimal separator from sample amount values."""
    comma_decimal = 0
    period_decimal = 0
    for value in sample_values[:20]:
        v = str(value).strip()
        if re.search(r",\d{1,2}$", v):
            comma_decimal += 1
        if re.search(r"\.\d{1,2}$", v):
            period_decimal += 1
    return "," if comma_decimal >= period_decimal else "."


# ─── Tier 3: LLM Fallback ────────────────────────────────────────────────────

def _redact_pii(value: str) -> str:
    """Redact PII from sample data before sending to LLM."""
    # Mask account numbers (sequences of 8+ digits)
    value = re.sub(r"\d{8,}", "****", value)
    # Mask email addresses
    value = re.sub(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+", "***@***.***", value)
    return value


def _prepare_llm_sample(
    columns: list[str],
    rows: list[list[str]],
    max_first: int = 5,
    max_last: int = 2,
) -> str:
    """Prepare a sample table string for LLM, with PII redacted."""
    header = " | ".join(columns)
    sample_rows = rows[:max_first]
    if len(rows) > max_first + max_last:
        sample_rows += rows[-max_last:]

    lines = [header, "-" * len(header)]
    for row in sample_rows:
        redacted = [_redact_pii(str(cell)) for cell in row]
        lines.append(" | ".join(redacted))

    return "\n".join(lines)


async def detect_columns_llm(
    columns: list[str],
    sample_rows: list[list[str]],
) -> Optional[MappingResult]:
    """
    Use LLM to detect column mapping when heuristic confidence is low.
    Returns None if LLM call fails.
    """
    try:
        import openai
    except ImportError:
        logger.warning("openai package not available for LLM column detection")
        return None

    sample_text = _prepare_llm_sample(columns, sample_rows)

    prompt = f"""You are analyzing a bank statement CSV file. Given the column headers and sample data below, identify which column maps to each field.

Columns: {columns}

Sample data:
{sample_text}

Return a JSON object mapping these fields to column names:
- date: the transaction date column
- description: the transaction description/memo column
- amount: single amount column (if exists, positive=credit, negative=debit)
- debit: debit/withdrawal column (if separate from credit)
- credit: credit/deposit column (if separate from debit)
- reference: reference/transaction ID column (optional)
- balance: running balance column (optional)
- date_format: detected date format (DD/MM/YYYY, YYYY-MM-DD, etc.)

Only include fields you are confident about. Use exact column names from the headers.
Return ONLY valid JSON, no explanation."""

    try:
        import os
        import json

        client = openai.AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        response = await client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
            max_tokens=300,
            response_format={"type": "json_object"},
        )

        content = response.choices[0].message.content
        if not content:
            return None

        data = json.loads(content)
        result = MappingResult(source="llm", overall_confidence=0.80)

        for field_name in ["date", "description", "amount", "debit", "credit", "reference", "balance"]:
            col_name = data.get(field_name)
            if col_name and col_name in columns:
                result.matches[field_name] = ColumnMatch(
                    field=field_name,
                    column_name=col_name,
                    confidence=0.80,
                    source="llm",
                )

        if "date_format" in data:
            result.date_format = data["date_format"]

        # Apply same debit/credit vs amount logic
        if "debit" in result.matches and "credit" in result.matches:
            result.matches.pop("amount", None)
        elif "amount" in result.matches:
            result.matches.pop("debit", None)
            result.matches.pop("credit", None)

        return result

    except Exception as e:
        logger.warning(f"LLM column detection failed: {e}")
        return None


# ─── Main Entry Point ────────────────────────────────────────────────────────

async def auto_detect_columns(
    tenant_id: str,
    columns: list[str],
    sample_rows: list[list[str]],
    pool=None,
) -> MappingResult:
    """
    3-tier column detection:
      1. Check saved templates (confidence=1.0)
      2. Heuristic regex matching (confidence varies)
      3. LLM fallback if heuristic confidence < 0.85
    """
    # Tier 1: Template lookup
    if pool:
        template = await lookup_mapping_template(tenant_id, columns, pool)
        if template and template.overall_confidence >= 0.9:
            logger.info(f"Column mapping from template (confidence={template.overall_confidence})")
            return template

    # Tier 2: Heuristic
    heuristic = detect_columns_heuristic(columns)

    # Detect date format and decimal separator from sample data
    if "date" in heuristic.matches and sample_rows:
        date_col_idx = columns.index(heuristic.matches["date"].column_name) if heuristic.matches["date"].column_name in columns else -1
        if date_col_idx >= 0:
            date_samples = [row[date_col_idx] for row in sample_rows if len(row) > date_col_idx]
            heuristic.date_format = detect_date_format(date_samples)

    amount_field = heuristic.matches.get("amount") or heuristic.matches.get("debit")
    if amount_field and sample_rows:
        amt_col_idx = columns.index(amount_field.column_name) if amount_field.column_name in columns else -1
        if amt_col_idx >= 0:
            amt_samples = [row[amt_col_idx] for row in sample_rows if len(row) > amt_col_idx]
            heuristic.decimal_separator = detect_decimal_separator(amt_samples)

    if heuristic.overall_confidence >= 0.85:
        logger.info(f"Column mapping from heuristic (confidence={heuristic.overall_confidence})")
        return heuristic

    # Tier 3: LLM fallback
    logger.info(f"Heuristic confidence {heuristic.overall_confidence} < 0.85, trying LLM...")
    llm_result = await detect_columns_llm(columns, sample_rows)
    if llm_result and llm_result.overall_confidence > heuristic.overall_confidence:
        logger.info(f"Using LLM column mapping (confidence={llm_result.overall_confidence})")
        return llm_result

    # Fall back to heuristic even if low confidence
    logger.info(f"Using heuristic fallback (confidence={heuristic.overall_confidence})")
    return heuristic
