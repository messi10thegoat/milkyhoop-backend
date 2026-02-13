"""
PlanGenerator - Core service for action_planner microservice.

Generates ActionPlan from user text via LLM classification and parsing.

IRON LAW 0 & 10: This service ONLY generates plans. It NEVER writes
accounting data. All data mutations happen downstream in Kernel services.
"""
import json
import logging
import re
import uuid
from typing import Any, Dict, List, Optional

import httpx

from ..config import settings
from ..prompts import get_active_prompt, get_examples_for
from ..utils.schema_validator import (
    validate_classify_result,
    validate_parse_result,
    VALID_ACTION_TYPES,
)

logger = logging.getLogger(__name__)


# =============================================================================
# KEYWORD FALLBACK TABLES
# Extracted from Sprint 1 prototype (action_service.py)
# =============================================================================
ACTION_KEYWORDS: Dict[str, List[str]] = {
    "CREATE_PURCHASE_INVOICE": [
        "faktur pembelian", "faktur beli", "catat faktur", "input faktur",
        "buat faktur pembelian", "terima faktur", "faktur dari",
        "purchase invoice", "bill", "tagihan dari",
    ],
    "CREATE_SALES_INVOICE": [
        "faktur penjualan", "invoice penjualan", "buat invoice",
        "tagihan ke", "faktur ke", "jual", "sales invoice",
    ],
    "CREATE_VENDOR": [
        "tambah vendor", "daftar vendor", "vendor baru",
        "tambah supplier", "supplier baru",
    ],
    "CREATE_CUSTOMER": [
        "tambah customer", "daftar customer", "customer baru",
        "tambah pelanggan", "pelanggan baru",
    ],
    "CREATE_PRODUCT": [
        "tambah produk", "produk baru", "barang baru",
        "daftarkan produk", "daftarkan barang", "tambah barang",
        "daftarkan item", "tambah jasa", "item baru",
        "daftarkan ke master data", "daftar produk",
        "tambah item", "input produk", "input barang", "catat produk",
    ],
    "MAKE_PAYMENT": [
        "bayar", "pembayaran", "transfer ke", "bayar tagihan",
        "lunasi", "bayar faktur",
    ],
    "RECEIVE_PAYMENT": [
        "terima pembayaran", "pelunasan", "terima bayar",
        "pembayaran masuk", "terima transfer",
    ],
    "CREATE_CREDIT_NOTE": [
        "nota kredit", "credit note", "retur penjualan", "retur jual",
        "buat nota kredit", "catat retur", "retur pelanggan",
        "kembalikan", "pengembalian barang", "retur barang",
    ],
    "UPDATE_VENDOR": [
        "ubah vendor", "update vendor", "ganti vendor",
        "edit vendor", "perbaiki vendor",
    ],
    "UPDATE_CUSTOMER": [
        "ubah pelanggan", "update pelanggan", "ganti pelanggan",
        "edit pelanggan", "edit customer", "ubah customer", "update customer",
    ],
    "UPDATE_PRODUCT": [
        "ubah produk", "update produk", "ganti harga",
        "edit produk", "edit barang", "ubah barang", "update barang",
    ],
    "CREATE_EXPENSE": [
        "catat biaya", "catat pengeluaran", "tambah biaya", "tambah pengeluaran",
        "expense", "bayar listrik", "bayar sewa", "bayar gaji",
        "biaya operasional", "biaya internet", "keluar biaya",
        "pengeluaran", "biaya transportasi", "biaya kantor",
    ],
    "BANK_TRANSFER": [
        "transfer bank", "transfer dari", "transfer ke",
        "pindah dana", "pindahkan saldo", "transfer antar rekening",
        "pindah uang", "kirim dana",
    ],
    "CREATE_PURCHASE_ORDER": [
        "pesanan pembelian", "purchase order", "buat po", "buat PO",
        "pesan barang", "order ke vendor", "order barang",
        "po baru", "pesanan baru",
    ],
}

READ_KEYWORDS = [
    "berapa", "apa", "lihat", "tampilkan", "cek", "saldo",
    "laporan", "report", "aging", "neraca", "kenapa", "mengapa",
    "stok", "stock", "total",
]

CONFIRM_KEYWORDS = [
    "lanjutkan", "ya", "yes", "ok", "oke", "setuju", "confirm", "proceed",
]

CANCEL_KEYWORDS = [
    "batal", "cancel", "tidak", "no", "jangan", "stop",
]

# Master data action types that use parse_master_data_text
_MASTER_DATA_ACTIONS = {
    "CREATE_VENDOR", "CREATE_CUSTOMER", "CREATE_PRODUCT",
    "UPDATE_VENDOR", "UPDATE_CUSTOMER", "UPDATE_PRODUCT",
}


class PlanGenerator:
    """
    Generates ActionPlan from user text.

    Iron Law 0 & 10: ONLY plans, NEVER writes data.
    All methods return data structures; no side effects on accounting data.
    """

    def __init__(self):
        self._http_client: Optional[httpx.AsyncClient] = None

    async def _get_client(self) -> httpx.AsyncClient:
        """Lazy-init a shared httpx client for OpenAI calls."""
        if self._http_client is None or self._http_client.is_closed:
            self._http_client = httpx.AsyncClient(timeout=30.0)
        return self._http_client

    async def close(self):
        """Close the HTTP client. Call on shutdown."""
        if self._http_client and not self._http_client.is_closed:
            await self._http_client.aclose()

    # =================================================================
    # PRIVATE: LLM CALL HELPER
    # =================================================================
    async def _call_openai(
        self,
        messages: List[Dict[str, str]],
        max_tokens: int,
        temperature: float,
        timeout: float,
    ) -> Optional[str]:
        """
        Make a single OpenAI chat completion call.

        Returns the assistant content string, or None on failure.
        """
        if not settings.OPENAI_API_KEY:
            logger.warning("OPENAI_API_KEY not set, skipping LLM call")
            return None

        client = await self._get_client()
        try:
            resp = await client.post(
                f"{settings.OPENAI_BASE_URL}/chat/completions",
                headers={
                    "Authorization": f"Bearer {settings.OPENAI_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": settings.OPENAI_MODEL,
                    "messages": messages,
                    "max_tokens": max_tokens,
                    "temperature": temperature,
                },
                timeout=timeout,
            )
            resp.raise_for_status()
            data = resp.json()
            content = data["choices"][0]["message"]["content"].strip()
            return content
        except httpx.TimeoutException:
            logger.error("OpenAI call timed out")
            return None
        except httpx.HTTPStatusError as e:
            logger.error(f"OpenAI HTTP error {e.response.status_code}: {e.response.text[:200]}")
            return None
        except Exception as e:
            logger.error(f"OpenAI call failed: {e}")
            return None

    @staticmethod
    def _strip_markdown_codeblock(text: str) -> str:
        """Strip ```json ... ``` wrappers that LLMs sometimes add."""
        text = text.strip()
        if text.startswith("```"):
            # Remove opening fence
            first_newline = text.find("\n")
            if first_newline != -1:
                text = text[first_newline + 1:]
            else:
                text = text[3:]
            # Remove closing fence
            if text.endswith("```"):
                text = text[:-3]
            text = text.strip()
        return text

    @staticmethod
    def _parse_json_response(raw: str) -> Optional[dict]:
        """Parse JSON from LLM response, stripping markdown wrappers."""
        cleaned = PlanGenerator._strip_markdown_codeblock(raw)
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError as e:
            logger.warning(f"JSON parse failed: {e}, raw={cleaned[:200]}")
            return None

    def _build_messages_with_examples(
        self,
        prompt_name: str,
        user_text: str,
        extra_system_context: Optional[str] = None,
    ) -> List[Dict[str, str]]:
        """
        Build the messages array with system prompt, few-shot examples,
        and the actual user message.
        """
        system_prompt = get_active_prompt(prompt_name)
        messages = [{"role": "system", "content": system_prompt}]

        if extra_system_context:
            messages.append({"role": "system", "content": f"Context: {extra_system_context}"})

        # Inject few-shot examples
        try:
            examples = get_examples_for(prompt_name)
            for ex in examples:
                messages.append({"role": "user", "content": ex["user"]})
                messages.append({"role": "assistant", "content": ex["assistant"]})
        except KeyError:
            pass  # No examples for this prompt type

        messages.append({"role": "user", "content": user_text})
        return messages

    # =================================================================
    # PUBLIC: CLASSIFY INTENT
    # =================================================================
    async def classify_intent(self, text: str) -> dict:
        """
        Classify user intent via LLM with keyword fallback.

        Returns:
            {
                "intent": "ACTION"|"READ"|"CONFIRM"|"CANCEL"|"UNCLEAR",
                "action_type": str | None,
                "confidence": float,
                "reason": str,
                "source": "llm" | "keyword",
            }
        """
        if not text or not text.strip():
            return {
                "intent": "UNCLEAR",
                "action_type": None,
                "confidence": 0.0,
                "reason": "Empty input",
                "source": "keyword",
            }

        # --- Try LLM classification ---
        messages = self._build_messages_with_examples("classify_intent", text)
        raw = await self._call_openai(
            messages=messages,
            max_tokens=settings.MAX_TOKENS_CLASSIFY,
            temperature=settings.TEMPERATURE_CLASSIFY,
            timeout=settings.LLM_TIMEOUT_CLASSIFY,
        )

        if raw:
            parsed = self._parse_json_response(raw)
            if parsed:
                is_valid, err = validate_classify_result(parsed)
                if is_valid:
                    confidence = parsed.get("confidence", 0.0)
                    if confidence >= settings.MIN_CONFIDENCE_LLM:
                        logger.info(
                            f"LLM classify: intent={parsed['intent']}, "
                            f"action={parsed.get('action_type')}, "
                            f"confidence={confidence}"
                        )
                        return {
                            "intent": parsed["intent"],
                            "action_type": parsed.get("action_type"),
                            "confidence": confidence,
                            "reason": parsed.get("reason", ""),
                            "source": "llm",
                        }
                    else:
                        logger.info(
                            f"LLM confidence too low ({confidence}), "
                            f"falling back to keywords"
                        )
                else:
                    logger.warning(f"LLM classify validation failed: {err}")

        # --- Keyword fallback ---
        return self._classify_by_keywords(text)

    def _classify_by_keywords(self, text: str) -> dict:
        """
        Keyword-based intent classification (fallback).
        Mirrors Sprint 1 logic from action_service.py.
        """
        text_lower = text.lower().strip()

        def _word_match(keyword: str, t: str) -> bool:
            if len(keyword) <= 3:
                return bool(re.search(r"\b" + re.escape(keyword) + r"\b", t))
            return keyword in t

        # Check ACTION keywords
        for action_type, keywords in ACTION_KEYWORDS.items():
            for kw in keywords:
                if _word_match(kw, text_lower):
                    return {
                        "intent": "ACTION",
                        "action_type": action_type,
                        "confidence": 0.7,
                        "reason": f"Keyword match: '{kw}'",
                        "source": "keyword",
                    }

        # Check CONFIRM keywords
        for kw in CONFIRM_KEYWORDS:
            if _word_match(kw, text_lower):
                return {
                    "intent": "CONFIRM",
                    "action_type": None,
                    "confidence": 0.8,
                    "reason": f"Keyword match: '{kw}'",
                    "source": "keyword",
                }

        # Check CANCEL keywords
        for kw in CANCEL_KEYWORDS:
            if _word_match(kw, text_lower):
                return {
                    "intent": "CANCEL",
                    "action_type": None,
                    "confidence": 0.8,
                    "reason": f"Keyword match: '{kw}'",
                    "source": "keyword",
                }

        # Check READ keywords
        for kw in READ_KEYWORDS:
            if _word_match(kw, text_lower):
                return {
                    "intent": "READ",
                    "action_type": None,
                    "confidence": 0.6,
                    "reason": f"Keyword match: '{kw}'",
                    "source": "keyword",
                }

        return {
            "intent": "UNCLEAR",
            "action_type": None,
            "confidence": 0.3,
            "reason": "No keyword or LLM match",
            "source": "keyword",
        }

    # =================================================================
    # PUBLIC: GENERATE PLAN
    # =================================================================
    async def generate_plan(
        self,
        text: str,
        intent: dict,
        context: Optional[dict] = None,
    ) -> dict:
        """
        Generate a structured ActionPlan from classified intent.

        Iron Law 0 & 10: Returns plan dict only. Never writes data.

        Args:
            text: Original user text.
            intent: Result from classify_intent().
            context: Optional context (conversation history, tenant info).

        Returns:
            ActionPlan dict with action_id, intent, confidence,
            draft_payload, assumptions, etc.
        """
        action_id = str(uuid.uuid4())
        intent_type = intent.get("intent", "UNCLEAR")
        action_type = intent.get("action_type")
        confidence = intent.get("confidence", 0.0)

        plan = {
            "action_id": action_id,
            "intent": intent_type,
            "action_type": action_type,
            "confidence": confidence,
            "draft_payload": {},
            "assumptions": [],
            "missing_fields": [],
            "clarification_needed": None,
            "requires_confirmation": False,
            "reason": intent.get("reason", ""),
        }

        # For ACTION intents, parse the text into structured data
        if intent_type == "ACTION" and action_type in VALID_ACTION_TYPES:
            if action_type in ("CREATE_PURCHASE_INVOICE", "CREATE_SALES_INVOICE", "CREATE_CREDIT_NOTE"):
                parsed = await self.parse_document_text(text, action_type)
                plan["draft_payload"] = parsed
                plan["missing_fields"] = parsed.get("missing_fields", [])
                plan["clarification_needed"] = parsed.get("clarification_needed")
                plan["requires_confirmation"] = True

                # Add default assumptions
                plan["assumptions"].append("PPN 11% (tarif default)")
                plan["assumptions"].append("Periode akuntansi masih terbuka")
                if not parsed.get("issue_date"):
                    plan["assumptions"].append("Tanggal faktur: hari ini")
                if not parsed.get("due_date"):
                    plan["assumptions"].append("Jatuh tempo: 30 hari dari tanggal faktur")

            elif action_type in _MASTER_DATA_ACTIONS:
                parse_result = await self.parse_master_data_text(text, action_type)
                draft_payload = parse_result.get("extracted_fields", {})

                # Map name field for downstream consistency
                name_val = draft_payload.pop("name", "")
                if "VENDOR" in action_type:
                    draft_payload["vendor_name"] = name_val
                elif "CUSTOMER" in action_type:
                    draft_payload["customer_name"] = name_val
                elif "PRODUCT" in action_type:
                    draft_payload["product_name"] = name_val

                plan["draft_payload"] = draft_payload
                plan["missing_fields"] = parse_result.get("missing_fields", [])
                plan["clarification_needed"] = (
                    "Data belum lengkap, mohon lengkapi: " + ", ".join(parse_result["missing_fields"])
                    if parse_result.get("clarification_needed")
                    else None
                )
                plan["assumptions"] = parse_result.get("assumptions", [])
                plan["requires_confirmation"] = True

            elif action_type in ("MAKE_PAYMENT", "RECEIVE_PAYMENT"):
                plan["draft_payload"] = {"text": text}
                plan["requires_confirmation"] = True

        return plan

    # =================================================================
    # PUBLIC: PARSE MASTER DATA TEXT
    # =================================================================
    async def parse_master_data_text(self, text: str, action_type: str) -> dict:
        """
        Parse master data creation/update text using LLM.

        Handles CREATE_VENDOR, CREATE_CUSTOMER, CREATE_PRODUCT,
        UPDATE_VENDOR, UPDATE_CUSTOMER, UPDATE_PRODUCT.

        Returns structured dict with extracted_fields, or fallback on failure.
        """
        try:
            messages = self._build_messages_with_examples(
                "parse_master_data",
                f"[{action_type}] {text}",
            )
            raw = await self._call_openai(
                messages=messages,
                max_tokens=500,
                temperature=0.1,
                timeout=settings.LLM_TIMEOUT_PARSE,
            )

            if raw:
                parsed = self._parse_json_response(raw)
                if parsed and isinstance(parsed, dict):
                    logger.info(
                        f"LLM master data parse: action={parsed.get('action_type')}, "
                        f"name={parsed.get('extracted_fields', {}).get('name')}"
                    )
                    return parsed

        except Exception as e:
            logger.warning(f"Master data parse failed: {e}")

        # Fallback: extract name from text via keyword stripping
        name = text.lower()
        strip_words = [
            "daftarkan", "tambah", "buat", "vendor", "pelanggan",
            "customer", "produk", "barang", "item", "baru", "sebagai",
            "supplier", "pemasok", "jasa",
        ]
        for kw in strip_words:
            name = name.replace(kw, "")
        name = " ".join(name.split()).strip().title()

        return {
            "action_type": action_type,
            "extracted_fields": {"name": name if name else text},
            "clarification_needed": False,
            "missing_fields": [],
            "assumptions": ["Parsed via keyword fallback"],
        }

        # =================================================================
    # PUBLIC: PARSE DOCUMENT TEXT
    # =================================================================
    async def parse_document_text(
        self, text: str, action_type: str
    ) -> dict:
        """
        Parse free-form text into document-specific structured data.

        Uses the parse_invoice prompt for purchase/sales invoices.
        Returns structured dict or empty defaults on failure.
        """
        messages = self._build_messages_with_examples("parse_invoice", text)
        raw = await self._call_openai(
            messages=messages,
            max_tokens=settings.MAX_TOKENS_PARSE,
            temperature=settings.TEMPERATURE_PARSE,
            timeout=settings.LLM_TIMEOUT_PARSE,
        )

        if raw:
            parsed = self._parse_json_response(raw)
            if parsed:
                is_valid, err = validate_parse_result(parsed)
                if is_valid:
                    logger.info(
                        f"LLM parse: counterparty={parsed.get('counterparty_name')}, "
                        f"items={len(parsed.get('items', []))}"
                    )
                    return parsed
                else:
                    logger.warning(f"LLM parse validation failed: {err}")
                    # Return the parsed data anyway (best effort) but log the issue
                    return parsed

        # Fallback: empty structure
        logger.warning("parse_document_text: LLM failed, returning empty structure")
        doc_type = (
            "purchase_invoice"
            if "PURCHASE" in action_type
            else "sales_invoice"
        )
        return {
            "document_type": doc_type,
            "counterparty_name": None,
            "invoice_number": None,
            "issue_date": None,
            "due_date": None,
            "tax_rate": 11,
            "tax_inclusive": False,
            "notes": "",
            "items": [],
            "missing_fields": ["counterparty_name", "items"],
            "clarification_needed": "Bisa kasih detail vendor/customer dan item yang mau dibuatkan fakturnya?",
        }

    # =================================================================
    # PUBLIC: GENERATE CONVERSATIONAL RESPONSE
    # =================================================================
    async def generate_response(
        self, text: str, context: Optional[str] = None
    ) -> str:
        """
        Generate natural conversational response as Milky assistant.

        Args:
            text: User message.
            context: Optional context string for the LLM.

        Returns:
            Response text, or a fallback string on failure.
        """
        messages = self._build_messages_with_examples(
            "conversational", text, extra_system_context=context
        )
        raw = await self._call_openai(
            messages=messages,
            max_tokens=settings.MAX_TOKENS_CONVO,
            temperature=settings.TEMPERATURE_CONVO,
            timeout=settings.LLM_TIMEOUT_CONVO,
        )

        if raw:
            return raw

        # Fallback
        return "Maaf, saya kurang paham. Bisa ceritakan lebih detail?"

    # =================================================================
    # PUBLIC: EXTRACT ENTITIES
    # =================================================================
    async def extract_entities(self, text: str) -> dict:
        """
        Extract vendor and product names from text for master data lookup.

        Returns:
            {"vendors": [...], "products": [...]}
        """
        system_prompt = get_active_prompt("extract_entities")
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": text},
        ]

        raw = await self._call_openai(
            messages=messages,
            max_tokens=settings.MAX_TOKENS_CLASSIFY,
            temperature=settings.TEMPERATURE_CLASSIFY,
            timeout=settings.LLM_TIMEOUT_CLASSIFY,
        )

        if raw:
            parsed = self._parse_json_response(raw)
            if parsed and isinstance(parsed, dict):
                return {
                    "vendors": parsed.get("vendors", []),
                    "products": parsed.get("products", []),
                }

        return {"vendors": [], "products": []}
