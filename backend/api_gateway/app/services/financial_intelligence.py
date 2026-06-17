"""
Financial Intelligence Engine (Read-Only)
==========================================
Phase 4: Analyze OCR output against existing financial data.

ZERO WRITE ACCESS. All queries are SELECT only.
Reads from:
  - journal_entries + journal_lines (AR/AP balances — Law 1, 16)
  - products + inventory_ledger (inventory matching)
  - chart_of_accounts (account recommendation — Law 27)
  - vendors / customers (counterparty matching)

NEVER reads from:
  - invoices.outstanding (denormalized — Law 16)
  - bills.amount_paid (denormalized — Law 16)

Output: analysis_result dict stored in uploaded_documents.analysis_result
"""
import logging
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ============================================================================
# CONSTANTS
# ============================================================================

# Unit aliases for compatibility matching
UNIT_ALIASES = {
    "pcs": ["pc", "piece", "buah", "bh", "biji", "unit"],
    "dus": ["box", "karton", "ctn", "carton"],
    "kg": ["kilogram", "kilo"],
    "ltr": ["liter", "l", "lt"],
    "mtr": ["meter", "m", "mt"],
    "roll": ["rol", "gulungan"],
    "set": ["paket", "kit"],
    "lembar": ["sheet", "lbr", "helai"],
    "batang": ["btg", "lonjor"],
    "sak": ["bag", "zak"],
}

# Keyword patterns for account recommendation (bilingual ID/EN)
KEYWORD_PATTERNS = [
    (["listrik", "pln", "token listrik", "electricity"], "Beban Listrik"),
    (["sewa", "rental", "kontrak sewa", "rent"], "Beban Sewa"),
    (["gaji", "salary", "upah", "honor", "honorarium"], "Beban Gaji"),
    (["telepon", "telkom", "internet", "wifi", "indihome"], "Beban Telepon"),
    (["asuransi", "insurance", "premi"], "Beban Asuransi"),
    (
        ["transport", "bensin", "solar", "tol", "parkir", "fuel", "grab", "gojek"],
        "Beban Transportasi",
    ),
    (["makan", "konsumsi", "catering", "meal", "snack"], "Beban Konsumsi"),
    (["atk", "alat tulis", "stationery", "kertas", "tinta"], "Beban ATK"),
    (
        ["pemeliharaan", "maintenance", "service", "servis", "perbaikan", "repair"],
        "Beban Pemeliharaan",
    ),
    (["pajak", "tax", "ppn", "pph"], "Beban Pajak"),
]

# Doc type → analysis routing
DOC_TYPE_ANALYSIS = {
    "invoice_purchase": {"ap_match": True, "inventory": True, "account_rec": True},
    "invoice_sales": {"ar_match": True, "inventory": True, "account_rec": False},
    "receipt": {"ap_match": False, "inventory": False, "account_rec": True},
    "bank_transfer_out": {"ap_match": True, "inventory": False, "account_rec": True},
    "bank_transfer_in": {"ar_match": True, "inventory": False, "account_rec": False},
    "bank_statement": {"ap_match": False, "inventory": False, "account_rec": False},
    "credit_note": {"ap_match": True, "inventory": False, "account_rec": False},
    "debit_note": {"ar_match": True, "inventory": False, "account_rec": False},
    "tax_document": {"ap_match": False, "inventory": False, "account_rec": False},
    "unknown": {"ap_match": False, "inventory": False, "account_rec": True},
}


def _safe_decimal(value) -> Optional[Decimal]:
    """Safely convert value to Decimal. Returns None on failure."""
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _str_amount(value) -> str:
    """Convert Decimal/number to string for JSONB storage (Law 25)."""
    if value is None:
        return "0"
    return str(value)


# ============================================================================
# MAIN CLASS
# ============================================================================


class FinancialIntelligence:
    """
    Read-only financial analysis engine.

    ZERO write access. All queries are SELECT only against:
    - journal_entries + journal_lines (AR/AP balances — Law 1, 16)
    - products + inventory_ledger (inventory matching)
    - chart_of_accounts (account recommendation — Law 27)
    """

    def __init__(self, conn):
        """
        Args:
            conn: asyncpg connection (should be read-only ideally)
        """
        self.conn = conn

    # ==================================================================
    # MAIN ORCHESTRATOR
    # ==================================================================

    async def analyze_document(
        self,
        tenant_id: str,
        document_id: str,
        ocr_result: Dict[str, Any],
        doc_type: str,
    ) -> Dict[str, Any]:
        """
        Orchestrate full analysis for one document.

        Pipeline:
        1. Determine analysis routing based on doc_type
        2. Run relevant analysis modules
        3. Run anomaly detection
        4. Return compiled analysis_result

        Returns: dict to be stored in uploaded_documents.analysis_result
        """
        routing = DOC_TYPE_ANALYSIS.get(doc_type, DOC_TYPE_ANALYSIS["unknown"])

        counterparty = ocr_result.get("counterparty_name") or ""
        total_amount = _safe_decimal(ocr_result.get("total_amount"))
        line_items = ocr_result.get("line_items") or []
        description_parts = [
            ocr_result.get("document_number", ""),
            counterparty,
        ]
        description = " - ".join(p for p in description_parts if p)

        result: Dict[str, Any] = {}

        # --- AR/AP matching ---
        if routing.get("ap_match") and counterparty and total_amount:
            try:
                ap_matches = await self.find_open_payables(
                    tenant_id, counterparty, total_amount
                )
                if ap_matches:
                    best = ap_matches[0]
                    result["ar_ap_match"] = {
                        "matched": True,
                        "match_type": "payable",
                        "match_confidence": best["score"],
                        "matched_source_id": best.get("source_id"),
                        "matched_source_type": best.get("source_type"),
                        "matched_description": best.get("description"),
                        "outstanding_amount": _str_amount(best.get("outstanding")),
                        "journal_date": best.get("journal_date"),
                        "is_partial_payment": (
                            total_amount < _safe_decimal(best.get("outstanding", 0))
                            if best.get("outstanding")
                            else False
                        ),
                        "alternatives": [
                            {
                                "source_id": m.get("source_id"),
                                "description": m.get("description"),
                                "outstanding": _str_amount(m.get("outstanding")),
                                "score": m["score"],
                            }
                            for m in ap_matches[1:4]
                        ],
                    }
                else:
                    result["ar_ap_match"] = {
                        "matched": False,
                        "match_type": "payable",
                        "match_confidence": "0",
                        "reason": "No open payables matching this vendor/amount",
                    }
            except Exception as e:
                logger.error(f"[FI] AP match failed for {document_id}: {e}")
                result["ar_ap_match"] = {
                    "matched": False,
                    "error": str(e)[:200],
                }

        elif routing.get("ar_match") and counterparty and total_amount:
            try:
                ar_matches = await self.find_open_receivables(
                    tenant_id, counterparty, total_amount
                )
                if ar_matches:
                    best = ar_matches[0]
                    result["ar_ap_match"] = {
                        "matched": True,
                        "match_type": "receivable",
                        "match_confidence": best["score"],
                        "matched_source_id": best.get("source_id"),
                        "matched_source_type": best.get("source_type"),
                        "matched_description": best.get("description"),
                        "outstanding_amount": _str_amount(best.get("outstanding")),
                        "journal_date": best.get("journal_date"),
                        "is_partial_payment": (
                            total_amount < _safe_decimal(best.get("outstanding", 0))
                            if best.get("outstanding")
                            else False
                        ),
                        "alternatives": [
                            {
                                "source_id": m.get("source_id"),
                                "description": m.get("description"),
                                "outstanding": _str_amount(m.get("outstanding")),
                                "score": m["score"],
                            }
                            for m in ar_matches[1:4]
                        ],
                    }
                else:
                    result["ar_ap_match"] = {
                        "matched": False,
                        "match_type": "receivable",
                        "match_confidence": "0",
                        "reason": "No open receivables matching this customer/amount",
                    }
            except Exception as e:
                logger.error(f"[FI] AR match failed for {document_id}: {e}")
                result["ar_ap_match"] = {
                    "matched": False,
                    "error": str(e)[:200],
                }

        # --- Inventory matching ---
        if routing.get("inventory") and line_items:
            try:
                inventory_matches = []
                for idx, item in enumerate(line_items):
                    match = await self.match_line_item_to_product(tenant_id, item)
                    match["line_index"] = idx
                    inventory_matches.append(match)
                result["inventory_matches"] = inventory_matches
            except Exception as e:
                logger.error(f"[FI] Inventory match failed for {document_id}: {e}")
                result["inventory_matches"] = []

        # --- Account recommendation ---
        if routing.get("account_rec"):
            try:
                account_rec = await self.recommend_account(
                    tenant_id,
                    doc_type,
                    counterparty,
                    description,
                    total_amount or Decimal("0"),
                )
                result["account_recommendation"] = account_rec
            except Exception as e:
                logger.error(f"[FI] Account rec failed for {document_id}: {e}")
                result["account_recommendation"] = {
                    "confidence": "0",
                    "error": str(e)[:200],
                }

        # --- Anomaly detection ---
        try:
            anomalies = await self.detect_anomalies(
                tenant_id,
                ocr_result,
                doc_type,
                matched_products=result.get("inventory_matches"),
                matched_ap_ar=result.get("ar_ap_match"),
            )
            result["anomalies"] = anomalies
        except Exception as e:
            logger.error(f"[FI] Anomaly detection failed for {document_id}: {e}")
            result["anomalies"] = []

        return result

    # ==================================================================
    # AR/AP BALANCE QUERIES (Law 1, Law 16)
    # ==================================================================

    async def find_open_receivables(
        self,
        tenant_id: str,
        customer_name: str,
        amount: Decimal,
    ) -> List[Dict[str, Any]]:
        """
        Find unpaid AR — FULL REROUTE to the canonical compute_ar_outstanding()
        (Law 16, single source of truth).

        FIX_P35_ARCANON 2026-06-17 — Layer 2 (Decision #4: full reroute, NOT mirror).
        Previously this maintained its own AR CTE that grouped journal_lines by
        je.source_id and did NOT count customer_deposit_applications — so after a
        deposit was applied it would over-state the open receivable and mis-suggest
        a match. It now reads canonical per-invoice outstanding (deposit-aware).

        Suggestion-only / document-intake matching — ZERO ledger impact. Scores by
        customer-name similarity + amount proximity. Return contract preserved:
        source_id (= invoice_id), source_type ('INVOICE'), description, outstanding,
        journal_date (= invoice_date), name_similarity, amount_score, score.
        """
        rows = await self.conn.fetch(
            """
            SELECT
                o.invoice_id::text       AS source_id,
                'INVOICE'                AS source_type,
                (o.invoice_number || ' - ' || COALESCE(o.customer_name, '')) AS description,
                o.invoice_date::text     AS journal_date,
                o.outstanding            AS outstanding,
                similarity(COALESCE(o.customer_name, ''), $2) AS name_sim,
                CASE
                    WHEN o.outstanding = $3 THEN 1.0
                    WHEN o.outstanding BETWEEN $3 * 0.95 AND $3 * 1.05 THEN 0.8
                    WHEN o.outstanding BETWEEN $3 * 0.80 AND $3 * 1.20 THEN 0.5
                    ELSE 0.1
                END AS amount_score
            FROM compute_ar_outstanding($1) o
            WHERE o.outstanding > 0
            ORDER BY
                (similarity(COALESCE(o.customer_name, ''), $2) * 0.3
                 + CASE WHEN o.outstanding = $3 THEN 0.4
                        WHEN o.outstanding BETWEEN $3 * 0.95 AND $3 * 1.05 THEN 0.32
                        WHEN o.outstanding BETWEEN $3 * 0.80 AND $3 * 1.20 THEN 0.2
                        ELSE 0.04 END
                 + CASE WHEN similarity(COALESCE(o.customer_name, ''), $2) > 0.3 THEN 0.3 ELSE 0.0 END
                ) DESC
            LIMIT 10
            """,
            tenant_id,
            customer_name,
            amount,
        )

        results = []
        for row in rows:
            name_sim = float(row["name_sim"])
            amount_score = float(row["amount_score"])
            combined = round(
                name_sim * 0.3 + amount_score * 0.4 + (0.3 if name_sim > 0.3 else 0.0),
                4,
            )
            results.append(
                {
                    "source_id": row["source_id"],
                    "source_type": row["source_type"],
                    "description": row["description"],
                    "journal_date": row["journal_date"],
                    "outstanding": _str_amount(row["outstanding"]),
                    "name_similarity": str(round(name_sim, 4)),
                    "amount_score": str(round(amount_score, 4)),
                    "score": str(combined),
                }
            )

        return results

    async def find_open_payables(
        self,
        tenant_id: str,
        vendor_name: str,
        amount: Decimal,
    ) -> List[Dict[str, Any]]:
        """
        Find unpaid AP from journal_lines (Law 16).
        AP outstanding = SUM(credit) - SUM(debit) (reversed from AR).
        """
        rows = await self.conn.fetch(
            """
            WITH ap_balances AS (
                SELECT
                    je.source_id,
                    je.source_type,
                    je.description,
                    je.journal_date::text AS journal_date,
                    SUM(jl.credit) - SUM(jl.debit) AS outstanding
                FROM journal_lines jl
                JOIN journal_entries je ON je.id = jl.journal_id
                JOIN chart_of_accounts coa ON coa.id = jl.account_id
                WHERE je.tenant_id = $1
                  AND je.status = 'POSTED'
                  AND coa.account_type = 'PAYABLE'
                GROUP BY je.source_id, je.source_type, je.description, je.journal_date
                HAVING SUM(jl.credit) - SUM(jl.debit) > 0
            )
            SELECT
                source_id::text,
                source_type,
                description,
                journal_date,
                outstanding,
                similarity(COALESCE(description, ''), $2) AS name_sim,
                CASE
                    WHEN outstanding = $3 THEN 1.0
                    WHEN outstanding BETWEEN $3 * 0.95 AND $3 * 1.05 THEN 0.8
                    WHEN outstanding BETWEEN $3 * 0.80 AND $3 * 1.20 THEN 0.5
                    ELSE 0.1
                END AS amount_score
            FROM ap_balances
            ORDER BY
                (similarity(COALESCE(description, ''), $2) * 0.3
                 + CASE WHEN outstanding = $3 THEN 0.4
                        WHEN outstanding BETWEEN $3 * 0.95 AND $3 * 1.05 THEN 0.32
                        WHEN outstanding BETWEEN $3 * 0.80 AND $3 * 1.20 THEN 0.2
                        ELSE 0.04 END
                 + CASE WHEN similarity(COALESCE(description, ''), $2) > 0.3 THEN 0.3 ELSE 0.0 END
                ) DESC
            LIMIT 10
            """,
            tenant_id,
            vendor_name,
            amount,
        )

        results = []
        for row in rows:
            name_sim = float(row["name_sim"])
            amount_score = float(row["amount_score"])
            combined = round(
                name_sim * 0.3 + amount_score * 0.4 + (0.3 if name_sim > 0.3 else 0.0),
                4,
            )
            results.append(
                {
                    "source_id": row["source_id"],
                    "source_type": row["source_type"],
                    "description": row["description"],
                    "journal_date": row["journal_date"],
                    "outstanding": _str_amount(row["outstanding"]),
                    "name_similarity": str(round(name_sim, 4)),
                    "amount_score": str(round(amount_score, 4)),
                    "score": str(combined),
                }
            )

        return results

    # ==================================================================
    # INVENTORY MATCHING
    # ==================================================================

    async def match_line_item_to_product(
        self,
        tenant_id: str,
        line_item: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Match OCR line item to existing product.

        Strategy 1: Exact match by item_code
        Strategy 2: Fuzzy name match via pg_trgm similarity()
        Strategy 3: No match → suggest new product creation
        """
        item_code = (line_item.get("item_code") or "").strip()
        item_desc = (line_item.get("description") or "").strip()
        unit_price = _safe_decimal(line_item.get("unit_price"))

        # Strategy 1: Exact code match
        if item_code:
            row = await self.conn.fetchrow(
                """
                SELECT id::text, nama_produk, item_code, satuan,
                       purchase_price, costing_method,
                       inventory_account_id::text, cogs_account_id::text
                FROM products
                WHERE tenant_id = $1
                  AND item_code = $2
                  AND (status IS NULL OR status = 'active')
                  AND deleted_at IS NULL
                LIMIT 1
                """,
                tenant_id,
                item_code,
            )
            if row:
                stock_info = await self._get_stock_info(tenant_id, row["id"])
                return {
                    "match_type": "exact",
                    "confidence": "0.98",
                    "product_id": row["id"],
                    "product_name": row["nama_produk"],
                    "product_code": row["item_code"],
                    "product_unit": row["satuan"],
                    "current_stock": _str_amount(stock_info.get("current_stock")),
                    "current_avg_cost": _str_amount(stock_info.get("avg_cost")),
                    "unit_mismatch": self._check_unit_mismatch(
                        line_item.get("unit"), row["satuan"]
                    ),
                    "invoice_unit": line_item.get("unit"),
                    "alternatives": [],
                    "suggestion": None,
                }

        # Strategy 2: Fuzzy name match
        if item_desc:
            rows = await self.conn.fetch(
                """
                SELECT
                    id::text,
                    nama_produk,
                    item_code,
                    satuan,
                    purchase_price,
                    similarity(nama_produk, $2) AS sim
                FROM products
                WHERE tenant_id = $1
                  AND (status IS NULL OR status = 'active')
                  AND deleted_at IS NULL
                  AND similarity(nama_produk, $2) > 0.15
                ORDER BY similarity(nama_produk, $2) DESC
                LIMIT 5
                """,
                tenant_id,
                item_desc,
            )

            if rows:
                best = rows[0]
                sim = float(best["sim"])

                # High confidence fuzzy match (sim > 0.5)
                if sim > 0.5:
                    stock_info = await self._get_stock_info(tenant_id, best["id"])
                    return {
                        "match_type": "fuzzy",
                        "confidence": str(min(round(sim * 0.95, 4), Decimal("0.95"))),
                        "product_id": best["id"],
                        "product_name": best["nama_produk"],
                        "product_code": best["item_code"],
                        "product_unit": best["satuan"],
                        "current_stock": _str_amount(stock_info.get("current_stock")),
                        "current_avg_cost": _str_amount(stock_info.get("avg_cost")),
                        "unit_mismatch": self._check_unit_mismatch(
                            line_item.get("unit"), best["satuan"]
                        ),
                        "invoice_unit": line_item.get("unit"),
                        "alternatives": [
                            {
                                "product_id": r["id"],
                                "product_name": r["nama_produk"],
                                "product_code": r["item_code"],
                                "similarity": str(round(float(r["sim"]), 4)),
                            }
                            for r in rows[1:4]
                        ],
                        "suggestion": None,
                    }

                # Low confidence — still return alternatives
                return {
                    "match_type": "fuzzy_low",
                    "confidence": str(round(sim * 0.7, 4)),
                    "product_id": best["id"],
                    "product_name": best["nama_produk"],
                    "product_code": best["item_code"],
                    "product_unit": best["satuan"],
                    "current_stock": None,
                    "current_avg_cost": None,
                    "unit_mismatch": False,
                    "invoice_unit": line_item.get("unit"),
                    "alternatives": [
                        {
                            "product_id": r["id"],
                            "product_name": r["nama_produk"],
                            "product_code": r["item_code"],
                            "similarity": str(round(float(r["sim"]), 4)),
                        }
                        for r in rows[1:4]
                    ],
                    "suggestion": await self._suggest_new_product(tenant_id, line_item),
                }

        # Strategy 3: No match
        suggestion = await self._suggest_new_product(tenant_id, line_item)
        return {
            "match_type": "none",
            "confidence": "0",
            "product_id": None,
            "product_name": None,
            "product_code": None,
            "product_unit": None,
            "current_stock": None,
            "current_avg_cost": None,
            "unit_mismatch": False,
            "invoice_unit": line_item.get("unit"),
            "alternatives": [],
            "suggestion": suggestion,
        }

    async def _get_stock_info(self, tenant_id: str, product_id: str) -> Dict[str, Any]:
        """Get current stock and avg cost from inventory_ledger."""
        # Current stock (sum of all movements)
        stock_row = await self.conn.fetchrow(
            """
            SELECT
                COALESCE(SUM(quantity_in) - SUM(quantity_out), 0) AS current_stock
            FROM inventory_ledger
            WHERE tenant_id = $1 AND product_id = $2::uuid
            """,
            tenant_id,
            product_id,
        )

        # Latest average cost
        cost_row = await self.conn.fetchrow(
            """
            SELECT average_cost
            FROM inventory_ledger
            WHERE tenant_id = $1 AND product_id = $2::uuid
            ORDER BY created_at DESC
            LIMIT 1
            """,
            tenant_id,
            product_id,
        )

        return {
            "current_stock": stock_row["current_stock"] if stock_row else Decimal("0"),
            "avg_cost": cost_row["average_cost"] if cost_row else None,
        }

    def _check_unit_mismatch(
        self, invoice_unit: Optional[str], product_unit: Optional[str]
    ) -> bool:
        """Check if invoice unit and product unit are compatible."""
        if not invoice_unit or not product_unit:
            return False

        inv = invoice_unit.strip().lower()
        prod = product_unit.strip().lower()

        if inv == prod:
            return False

        # Check aliases
        for canonical, aliases in UNIT_ALIASES.items():
            all_forms = [canonical] + aliases
            if inv in all_forms and prod in all_forms:
                return False

        return True

    async def _suggest_new_product(
        self, tenant_id: str, line_item: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Suggest defaults for creating a new product (Law 27 compliant)."""
        # Resolve inventory account at runtime
        inv_account = await self.conn.fetchrow(
            """
            SELECT id::text, account_code, name
            FROM chart_of_accounts
            WHERE tenant_id = $1
              AND account_type = 'INVENTORY'
              AND is_active = true
              AND is_header = false
            ORDER BY account_code
            LIMIT 1
            """,
            tenant_id,
        )

        # Resolve COGS account at runtime
        cogs_account = await self.conn.fetchrow(
            """
            SELECT id::text, account_code, name
            FROM chart_of_accounts
            WHERE tenant_id = $1
              AND account_type = 'COST_OF_GOODS_SOLD'
              AND is_active = true
              AND is_header = false
            ORDER BY account_code
            LIMIT 1
            """,
            tenant_id,
        )

        return {
            "suggested_name": line_item.get("description", ""),
            "suggested_unit": line_item.get("unit") or "pcs",
            "suggested_initial_cost": str(line_item.get("unit_price") or "0"),
            "suggested_inventory_account_id": inv_account["id"]
            if inv_account
            else None,
            "suggested_inventory_account_code": inv_account["account_code"]
            if inv_account
            else None,
            "suggested_cogs_account_id": cogs_account["id"] if cogs_account else None,
            "suggested_cogs_account_code": cogs_account["account_code"]
            if cogs_account
            else None,
            "missing_accounts": {
                "inventory": inv_account is None,
                "cogs": cogs_account is None,
            },
        }

    # ==================================================================
    # ACCOUNT RECOMMENDATION ENGINE
    # ==================================================================

    async def recommend_account(
        self,
        tenant_id: str,
        doc_type: str,
        counterparty_name: str,
        description: str,
        amount: Decimal,
    ) -> Dict[str, Any]:
        """
        3-layer account recommendation.

        Layer 1: Historical pattern (same vendor → same account, confidence 0.90)
        Layer 2: Keyword detection (scan for known expense keywords, confidence 0.75)
        Layer 3: Default by doc_type (confidence 0.50)

        All accounts resolved at runtime from chart_of_accounts (Law 27).
        """
        # Layer 1: Historical pattern
        if counterparty_name:
            historical = await self._find_historical_account(
                tenant_id, counterparty_name
            )
            if historical:
                return historical

        # Layer 2: Keyword detection
        combined_text = f"{counterparty_name} {description}".lower()
        keyword_match = await self._match_keyword_account(tenant_id, combined_text)
        if keyword_match:
            return keyword_match

        # Layer 3: Default by doc_type
        default = await self._default_account_for_doc_type(tenant_id, doc_type)
        if default:
            return default

        # Layer 4: No recommendation
        return {
            "account_id": None,
            "account_code": None,
            "account_name": None,
            "confidence": "0",
            "reasoning": "Tidak cukup data untuk rekomendasi akun.",
            "source": "none",
        }

    async def _find_historical_account(
        self, tenant_id: str, counterparty_name: str
    ) -> Optional[Dict[str, Any]]:
        """
        Layer 1: Find most commonly used account for this counterparty.
        Requires at least 2 historical postings.
        """
        rows = await self.conn.fetch(
            """
            SELECT
                coa.id::text AS account_id,
                coa.account_code,
                coa.name AS account_name,
                COUNT(*) AS usage_count
            FROM journal_lines jl
            JOIN journal_entries je ON je.id = jl.journal_id
            JOIN chart_of_accounts coa ON coa.id = jl.account_id
            WHERE je.tenant_id = $1
              AND je.status = 'POSTED'
              AND similarity(COALESCE(je.description, ''), $2) > 0.3
              AND coa.account_type NOT IN ('PAYABLE', 'RECEIVABLE')
              AND coa.is_header = false
            GROUP BY coa.id, coa.account_code, coa.name
            HAVING COUNT(*) >= 2
            ORDER BY COUNT(*) DESC
            LIMIT 1
            """,
            tenant_id,
            counterparty_name,
        )

        if rows:
            row = rows[0]
            return {
                "account_id": row["account_id"],
                "account_code": row["account_code"],
                "account_name": row["account_name"],
                "confidence": "0.90",
                "reasoning": (
                    f"Vendor '{counterparty_name}' sebelumnya diposting "
                    f"{row['usage_count']}x ke akun {row['account_code']}."
                ),
                "source": "historical_pattern",
            }

        return None

    async def _match_keyword_account(
        self, tenant_id: str, text: str
    ) -> Optional[Dict[str, Any]]:
        """
        Layer 2: Match keywords in description to known expense accounts.
        """
        for keywords, account_name_pattern in KEYWORD_PATTERNS:
            if any(kw in text for kw in keywords):
                row = await self.conn.fetchrow(
                    """
                    SELECT id::text, account_code, name
                    FROM chart_of_accounts
                    WHERE tenant_id = $1
                      AND account_type IN ('EXPENSE', 'OTHER_EXPENSE')
                      AND name ILIKE '%' || $2 || '%'
                      AND is_active = true
                      AND is_header = false
                    ORDER BY account_code
                    LIMIT 1
                    """,
                    tenant_id,
                    account_name_pattern,
                )
                if row:
                    matched_kw = next(kw for kw in keywords if kw in text)
                    return {
                        "account_id": row["id"],
                        "account_code": row["account_code"],
                        "account_name": row["name"],
                        "confidence": "0.75",
                        "reasoning": (
                            f"Kata kunci '{matched_kw}' terdeteksi, "
                            f"cocok dengan akun {row['account_code']} ({row['name']})."
                        ),
                        "source": "keyword_detection",
                    }

        return None

    async def _default_account_for_doc_type(
        self, tenant_id: str, doc_type: str
    ) -> Optional[Dict[str, Any]]:
        """
        Layer 3: Default account based on document type.
        """
        type_mapping = {
            "receipt": ("EXPENSE", "Beban"),
            "invoice_purchase": ("PAYABLE", None),
            "invoice_sales": ("RECEIVABLE", None),
            "bank_transfer_out": ("EXPENSE", None),
            "unknown": ("EXPENSE", "Beban"),
        }

        mapping = type_mapping.get(doc_type)
        if not mapping:
            return None

        account_type, name_hint = mapping

        if name_hint:
            row = await self.conn.fetchrow(
                """
                SELECT id::text, account_code, name
                FROM chart_of_accounts
                WHERE tenant_id = $1
                  AND account_type = $2
                  AND name ILIKE '%' || $3 || '%'
                  AND is_active = true
                  AND is_header = false
                ORDER BY account_code
                LIMIT 1
                """,
                tenant_id,
                account_type,
                name_hint,
            )
        else:
            row = await self.conn.fetchrow(
                """
                SELECT id::text, account_code, name
                FROM chart_of_accounts
                WHERE tenant_id = $1
                  AND account_type = $2
                  AND is_active = true
                  AND is_header = false
                ORDER BY account_code
                LIMIT 1
                """,
                tenant_id,
                account_type,
            )

        if row:
            return {
                "account_id": row["id"],
                "account_code": row["account_code"],
                "account_name": row["name"],
                "confidence": "0.50",
                "reasoning": (
                    f"Akun default untuk tipe dokumen '{doc_type}': "
                    f"{row['account_code']} ({row['name']})."
                ),
                "source": "default_by_doc_type",
            }

        return None

    # ==================================================================
    # ANOMALY DETECTION
    # ==================================================================

    async def detect_anomalies(
        self,
        tenant_id: str,
        ocr_result: Dict[str, Any],
        doc_type: str,
        matched_products: Optional[List[Dict]] = None,
        matched_ap_ar: Optional[Dict] = None,
    ) -> List[Dict[str, Any]]:
        """
        Detect financial anomalies and warnings.
        Returns list of anomaly dicts.
        """
        anomalies: List[Dict[str, Any]] = []

        counterparty = ocr_result.get("counterparty_name") or ""
        total_amount = _safe_decimal(ocr_result.get("total_amount"))
        line_items = ocr_result.get("line_items") or []

        # 1. Price anomaly: unit_price > 3x historical avg
        if matched_products:
            for match in matched_products:
                if (
                    match.get("match_type") in ("exact", "fuzzy")
                    and match.get("product_id")
                    and match.get("current_avg_cost")
                ):
                    avg_cost = _safe_decimal(match["current_avg_cost"])
                    li_idx = match.get("line_index", 0)
                    if li_idx < len(line_items):
                        invoice_price = _safe_decimal(
                            line_items[li_idx].get("unit_price")
                        )
                        if avg_cost and invoice_price and avg_cost > 0:
                            ratio = invoice_price / avg_cost
                            if ratio > 3:
                                anomalies.append(
                                    {
                                        "type": "price_anomaly",
                                        "severity": "warning",
                                        "message": (
                                            f"Harga {line_items[li_idx].get('description', 'item')} "
                                            f"({_str_amount(invoice_price)}) "
                                            f"adalah {round(float(ratio), 1)}x lipat "
                                            f"dari rata-rata ({_str_amount(avg_cost)})."
                                        ),
                                        "details": {
                                            "line_index": li_idx,
                                            "current_price": _str_amount(invoice_price),
                                            "avg_price": _str_amount(avg_cost),
                                            "ratio": str(round(float(ratio), 2)),
                                        },
                                    }
                                )

        # 2. Duplicate payment: same vendor + similar amount within 7 days
        if (
            counterparty
            and total_amount
            and doc_type in ("invoice_purchase", "bank_transfer_out", "receipt")
        ):
            dup_rows = await self.conn.fetch(
                """
                SELECT
                    je.source_id::text,
                    je.description,
                    je.journal_date::text
                FROM journal_lines jl
                JOIN journal_entries je ON je.id = jl.journal_id
                JOIN chart_of_accounts coa ON coa.id = jl.account_id
                WHERE je.tenant_id = $1
                  AND je.status = 'POSTED'
                  AND coa.account_type = 'PAYABLE'
                  AND similarity(COALESCE(je.description, ''), $2) > 0.3
                  AND je.journal_date >= CURRENT_DATE - INTERVAL '7 days'
                  AND jl.credit BETWEEN $3 * 0.95 AND $3 * 1.05
                LIMIT 5
                """,
                tenant_id,
                counterparty,
                total_amount,
            )
            if dup_rows:
                anomalies.append(
                    {
                        "type": "duplicate_payment",
                        "severity": "warning",
                        "message": (
                            f"Ditemukan {len(dup_rows)} transaksi serupa "
                            f"untuk '{counterparty}' dalam 7 hari terakhir."
                        ),
                        "details": {
                            "similar_transactions": [
                                {
                                    "source_id": r["source_id"],
                                    "description": r["description"],
                                    "journal_date": r["journal_date"],
                                }
                                for r in dup_rows
                            ],
                        },
                    }
                )

        # 3. New vendor/customer: first time seen
        if counterparty:
            vendor_exists = await self.conn.fetchval(
                """
                SELECT EXISTS(
                    SELECT 1 FROM vendors
                    WHERE tenant_id = $1
                      AND similarity(name, $2) > 0.5
                )
                """,
                tenant_id,
                counterparty,
            )
            if not vendor_exists:
                customer_exists = await self.conn.fetchval(
                    """
                    SELECT EXISTS(
                        SELECT 1 FROM customers
                        WHERE tenant_id = $1
                          AND similarity(nama, $2) > 0.5
                    )
                    """,
                    tenant_id,
                    counterparty,
                )
                if not customer_exists:
                    anomalies.append(
                        {
                            "type": "new_counterparty",
                            "severity": "info",
                            "message": (
                                f"'{counterparty}' belum terdaftar sebagai "
                                f"vendor atau pelanggan."
                            ),
                            "details": {
                                "counterparty_name": counterparty,
                            },
                        }
                    )

        # 4. Missing master data (products not found)
        if matched_products:
            no_match_count = sum(
                1 for m in matched_products if m.get("match_type") == "none"
            )
            if no_match_count > 0:
                anomalies.append(
                    {
                        "type": "missing_master_data",
                        "severity": "info",
                        "message": (
                            f"{no_match_count} item tidak ditemukan di master data produk."
                        ),
                        "details": {
                            "unmatched_items": [
                                {
                                    "line_index": m.get("line_index"),
                                    "description": (
                                        line_items[m["line_index"]].get("description")
                                        if m.get("line_index") is not None
                                        and m["line_index"] < len(line_items)
                                        else None
                                    ),
                                }
                                for m in matched_products
                                if m.get("match_type") == "none"
                            ],
                        },
                    }
                )

        # 5. Unit mismatch detected
        if matched_products:
            unit_mismatches = [
                m for m in matched_products if m.get("unit_mismatch") is True
            ]
            if unit_mismatches:
                anomalies.append(
                    {
                        "type": "unit_mismatch",
                        "severity": "warning",
                        "message": (
                            f"{len(unit_mismatches)} item memiliki satuan "
                            f"berbeda dari master data."
                        ),
                        "details": {
                            "mismatches": [
                                {
                                    "line_index": m.get("line_index"),
                                    "invoice_unit": m.get("invoice_unit"),
                                    "product_unit": m.get("product_unit"),
                                    "product_name": m.get("product_name"),
                                }
                                for m in unit_mismatches
                            ],
                        },
                    }
                )

        # 6. No AP/AR match for payment-type documents
        if (
            matched_ap_ar
            and not matched_ap_ar.get("matched")
            and doc_type in ("bank_transfer_out", "bank_transfer_in")
        ):
            anomalies.append(
                {
                    "type": "no_obligation_match",
                    "severity": "info",
                    "message": (
                        "Transfer ini tidak cocok dengan piutang/hutang yang ada. "
                        "Mungkin pembayaran non-invoice."
                    ),
                    "details": {},
                }
            )

        return anomalies
