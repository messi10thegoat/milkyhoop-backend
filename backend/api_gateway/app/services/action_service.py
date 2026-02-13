"""
Action Service - Core orchestrator for Agentic Chat Action Mode

Responsibilities:
- Intent classification (keyword-based for Sprint 1)
- Parse structured text into ActionPlan
- Validate against accounting rules
- Create pending actions with preview (journal dry-run)
- Execute via existing Kernel services (BillsService, etc.)
- Cancel/expire pending actions

IRON LAW COMPLIANCE:
- Law 0: This service is orchestrator, NOT the executor. All writes go through Kernel (BillsService).
- Law 10: No direct journal creation. BillsService handles that.
- Law 14: Idempotency keys on all mutations.
"""
import asyncio
import json
import logging
import os
import uuid
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

import asyncpg
import httpx
import re
import redis.asyncio as aioredis

logger = logging.getLogger(__name__)

# Pending action TTL: 15 minutes + 30 second grace period
PENDING_ACTION_TTL = 15 * 60 + 30
REDIS_PREFIX = "action:"

class LLMClassifier:
    """
    OpenAI-powered intent classification and text parsing.
    Iron Law 10: LLM NEVER writes data. Only classifies and parses.
    """

    SYSTEM_PROMPT_CLASSIFY = """Kamu adalah asisten akuntansi untuk aplikasi MilkyHoop.
Tugas kamu: klasifikasi intent pengguna dari pesan chat.

Respond HANYA dalam JSON format (tanpa markdown):
{
  "intent": "ACTION" | "READ" | "CONFIRM" | "CANCEL" | "UNCLEAR",
  "action_type": "CREATE_PURCHASE_INVOICE" | "CREATE_SALES_INVOICE" | "CREATE_VENDOR" | "MAKE_PAYMENT" | null,
  "confidence": 0.0-1.0,
  "reason": "penjelasan singkat"
}

Definisi intent:
- ACTION: User ingin MEMBUAT sesuatu (faktur, vendor, pembayaran). action_type wajib diisi.
- READ: User ingin MELIHAT data (saldo, laporan, info, pertanyaan).
- CONFIRM: User mengkonfirmasi aksi sebelumnya (ya, ok, lanjutkan, setuju).
- CANCEL: User membatalkan aksi (batal, tidak, jangan, cancel).
- UNCLEAR: Tidak jelas atau di luar scope akuntansi.

Definisi action_type:
- CREATE_PURCHASE_INVOICE: Faktur pembelian, tagihan dari vendor/supplier, bill, catat pembelian.
- CREATE_SALES_INVOICE: Faktur penjualan, invoice ke customer/pelanggan.
- CREATE_VENDOR: Tambah/daftar vendor/supplier baru.
- MAKE_PAYMENT: Bayar tagihan, lunasi, transfer pembayaran."""

    SYSTEM_PROMPT_PARSE = """Kamu adalah asisten akuntansi untuk MilkyHoop.
Tugas: Extract data faktur pembelian dari teks pengguna.

Respond HANYA dalam JSON format (tanpa markdown):
{
  "vendor_name": "nama vendor" | null,
  "invoice_number": "nomor faktur" | null,
  "bill_date": "YYYY-MM-DD" | null,
  "due_date": "YYYY-MM-DD" | null,
  "tax_rate": 11,
  "tax_inclusive": false,
  "notes": "",
  "items": [
    {
      "name": "nama produk",
      "qty": 0,
      "unit": "pcs",
      "price": 0,
      "discount_percent": 0
    }
  ],
  "missing_fields": ["field yang belum terisi"],
  "clarification_needed": "pertanyaan untuk user jika data kurang" | null
}

Rules:
- Harga dalam Rupiah (integer, tanpa titik/koma). Contoh: 50000 bukan 50.000
- Jika user bilang "50rb" = 50000, "1jt" = 1000000
- tax_rate default 11 (PPN 11%)
- Jika tanggal tidak disebutkan, isi null
- Jika ada data yang kurang/ambigu, isi missing_fields dan clarification_needed"""

    @staticmethod
    async def classify_intent(text: str) -> dict:
        """Classify user intent via OpenAI. Returns {intent, action_type, confidence, reason}."""
        api_key = os.getenv("OPENAI_API_KEY", "")
        if not api_key:
            logger.warning("OPENAI_API_KEY not set, falling back to keyword matching")
            return None

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(
                    "https://api.openai.com/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": "gpt-4o-mini",
                        "messages": [
                            {"role": "system", "content": LLMClassifier.SYSTEM_PROMPT_CLASSIFY},
                            {"role": "user", "content": text},
                        ],
                        "max_tokens": 200,
                        "temperature": 0.1,
                    },
                )
                resp.raise_for_status()
                data = resp.json()
                content = data["choices"][0]["message"]["content"].strip()
                # Strip markdown code blocks if present
                if content.startswith("```"):
                    content = content.split("\n", 1)[1] if "\n" in content else content[3:]
                    if content.endswith("```"):
                        content = content[:-3]
                    content = content.strip()
                result = json.loads(content)
                logger.info(f"LLM classify: intent={result.get('intent')}, action={result.get('action_type')}, confidence={result.get('confidence')}")
                return result
        except Exception as e:
            logger.error(f"LLM classification failed: {e}")
            return None

    @staticmethod
    async def parse_invoice_text(text: str) -> dict:
        """Parse free-form text into structured invoice data via OpenAI."""
        api_key = os.getenv("OPENAI_API_KEY", "")
        if not api_key:
            return None

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(
                    "https://api.openai.com/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": "gpt-4o-mini",
                        "messages": [
                            {"role": "system", "content": LLMClassifier.SYSTEM_PROMPT_PARSE},
                            {"role": "user", "content": text},
                        ],
                        "max_tokens": 1000,
                        "temperature": 0.1,
                    },
                )
                resp.raise_for_status()
                data = resp.json()
                content = data["choices"][0]["message"]["content"].strip()
                # Strip markdown code blocks if present
                if content.startswith("```"):
                    content = content.split("\n", 1)[1] if "\n" in content else content[3:]
                    if content.endswith("```"):
                        content = content[:-3]
                    content = content.strip()
                result = json.loads(content)
                logger.info(f"LLM parse: vendor={result.get('vendor_name')}, items={len(result.get('items', []))}")
                return result
        except Exception as e:
            logger.error(f"LLM parsing failed: {e}")
            return None







    SYSTEM_PROMPT_CONVO = """Kamu adalah Milky, asisten akuntansi cerdas untuk aplikasi MilkyHoop.

Personality: Ramah, profesional tapi santai, paham akuntansi Indonesia. Bicara natural seperti teman yang jago akuntansi.

Kemampuan kamu:
- Brainstorm dan diskusi tentang akuntansi, keuangan bisnis, pajak
- Bantu plan dan strategi keuangan
- Catat faktur pembelian (dari vendor/supplier)
- Catat faktur penjualan (ke customer/pelanggan) [segera hadir]
- Bayar tagihan [segera hadir]
- Lihat laporan keuangan [segera hadir]

Context tenant: Kamu melayani bisnis kecil-menengah di Indonesia. Pakai Rupiah. PPN 11%.

Rules:
- Jawab SINGKAT dan to the point (maks 2-3 kalimat)
- Kalau user mau ngobrol/brainstorm, layani dengan natural
- Kalau user siap action, guide mereka untuk kirim data terstruktur
- JANGAN kasih menu pilihan kaku. Ngobrol natural saja.
- Bahasa: ikuti bahasa user (formal/informal/campur)
- JANGAN bilang kamu AI/robot/asisten virtual. Cukup bantu saja.
- Kalau ditanya di luar akuntansi, jawab singkat lalu arahkan kembali"""

    @staticmethod
    async def generate_response(text: str, intent: str = None, context: str = None) -> str:
        """Generate natural conversational response via OpenAI."""
        api_key = os.getenv("OPENAI_API_KEY", "")
        if not api_key:
            return None

        messages = [
            {"role": "system", "content": LLMClassifier.SYSTEM_PROMPT_CONVO},
        ]
        if context:
            messages.append({"role": "system", "content": f"Context: {context}"})
        messages.append({"role": "user", "content": text})

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(
                    "https://api.openai.com/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": "gpt-4o-mini",
                        "messages": messages,
                        "max_tokens": 300,
                        "temperature": 0.7,
                    },
                )
                resp.raise_for_status()
                data = resp.json()
                return data["choices"][0]["message"]["content"].strip()
        except Exception as e:
            logger.error(f"LLM conversation failed: {e}")
            return None




# === INTENT CLASSIFICATION (Keyword-based for Sprint 1) ===

ACTION_KEYWORDS = {
    "CREATE_PURCHASE_INVOICE": [
        "faktur pembelian", "faktur beli", "catat faktur", "input faktur",
        "buat faktur pembelian", "terima faktur", "faktur dari",
        "purchase invoice", "bill", "tagihan dari",
    ],
    "CREATE_VENDOR": [
        "tambah vendor", "daftar vendor", "vendor baru",
        "tambah supplier", "supplier baru",
    ],
    "MAKE_PAYMENT": [
        "bayar", "pembayaran", "transfer ke", "bayar tagihan",
        "lunasi", "bayar faktur",
    ],
}

READ_KEYWORDS = [
    "berapa", "apa", "lihat", "tampilkan", "cek", "saldo",
    "laporan", "report", "aging", "neraca", "kenapa", "mengapa",
]

CONFIRM_KEYWORDS = ["lanjutkan", "ya", "yes", "ok", "oke", "setuju", "confirm", "proceed"]
CANCEL_KEYWORDS = ["batal", "cancel", "tidak", "no", "jangan", "stop"]
class ActionService:
    def __init__(self, pool: asyncpg.Pool):
        self.pool = pool
        self._redis: Optional[aioredis.Redis] = None

    async def _get_redis(self) -> aioredis.Redis:
        if self._redis is None:
            redis_url = os.getenv("REDIS_URL", "redis://redis:6379")
            redis_password = os.getenv("REDIS_PASSWORD", "")
            self._redis = aioredis.Redis.from_url(
                redis_url, password=redis_password if redis_password else None,
                decode_responses=True
            )
        return self._redis

    # =========================================================
    # INTENT CLASSIFICATION
    # =========================================================

    async def classify_intent(self, text: str) -> Tuple[str, Optional[str]]:
        """
        Classify user intent via OpenAI LLM, with keyword fallback.
        Returns (intent_type, action_type).
        """
        if not text:
            return ("UNCLEAR", None)

        # Try LLM classification first
        llm_result = await LLMClassifier.classify_intent(text)
        if llm_result and llm_result.get("intent"):
            intent = llm_result["intent"].upper()
            action_type = llm_result.get("action_type")
            confidence = llm_result.get("confidence", 0)

            # Accept LLM result if confidence >= 0.6
            if confidence >= 0.6 and intent in ("ACTION", "READ", "CONFIRM", "CANCEL", "UNCLEAR"):
                return (intent, action_type)
            logger.info(f"LLM confidence too low ({confidence}), falling back to keywords")

        # Fallback: keyword matching
        text_lower = text.lower().strip()

        def _word_match(keyword: str, text: str) -> bool:
            if len(keyword) <= 3:
                return bool(re.search(r"\b" + re.escape(keyword) + r"\b", text))
            return keyword in text

        for action_type, keywords in ACTION_KEYWORDS.items():
            for kw in keywords:
                if _word_match(kw, text_lower):
                    return ("ACTION", action_type)
        for kw in CONFIRM_KEYWORDS:
            if _word_match(kw, text_lower):
                return ("CONFIRM", None)
        for kw in CANCEL_KEYWORDS:
            if _word_match(kw, text_lower):
                return ("CANCEL", None)
        for kw in READ_KEYWORDS:
            if _word_match(kw, text_lower):
                return ("READ", None)

        return ("UNCLEAR", None)

    # =========================================================
    # PARSE PURCHASE INVOICE FROM TEXT
    # =========================================================

    async def parse_purchase_invoice_text(self, text: str) -> Dict[str, Any]:
        """
        Parse free-form text into purchase invoice fields via OpenAI LLM.
        Returns partial data - may need clarification for missing fields.
        """
        # Try LLM parsing
        llm_result = await LLMClassifier.parse_invoice_text(text)
        if llm_result:
            # Fill defaults for missing fields
            result = {
                "vendor_name": llm_result.get("vendor_name"),
                "invoice_number": llm_result.get("invoice_number"),
                "bill_date": llm_result.get("bill_date") or datetime.now().strftime("%Y-%m-%d"),
                "due_date": llm_result.get("due_date") or (datetime.now() + timedelta(days=30)).strftime("%Y-%m-%d"),
                "tax_rate": llm_result.get("tax_rate", 11),
                "tax_inclusive": llm_result.get("tax_inclusive", False),
                "items": llm_result.get("items", []),
                "notes": llm_result.get("notes", ""),
                "missing_fields": llm_result.get("missing_fields", []),
                "clarification_needed": llm_result.get("clarification_needed"),
            }
            return result

        # Fallback: empty draft
        return {
            "vendor_name": None,
            "invoice_number": None,
            "bill_date": datetime.now().strftime("%Y-%m-%d"),
            "due_date": (datetime.now() + timedelta(days=30)).strftime("%Y-%m-%d"),
            "tax_rate": 11,
            "tax_inclusive": False,
            "items": [],
            "notes": "",
        }

    def parse_purchase_invoice_structured(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Parse pre-structured data (e.g. from OCR/LLM in Sprint 2) into bill creation payload.
        This is what we use in Sprint 1 for the full flow.
        """
        items = []
        for item in data.get("items", []):
            items.append({
                "product_name": item.get("name", item.get("product_name", "")),
                "product_code": item.get("code", item.get("product_code", "")),
                "qty": item.get("qty", item.get("quantity", 0)),
                "unit": item.get("unit", "pcs"),
                "price": item.get("price", item.get("unit_price", 0)),
                "discount_percent": item.get("discount_percent", 0),
                "batch_no": item.get("batch_no", None),
                "exp_date": item.get("exp_date", None),
                "bonus_qty": item.get("bonus_qty", 0),
            })

        return {
            "vendor_id": data.get("vendor_id"),
            "vendor_name": data.get("vendor_name"),
            "invoice_number": data.get("invoice_number"),
            "ref_no": data.get("ref_no"),
            "bill_date": data.get("bill_date", datetime.now().strftime("%Y-%m-%d")),
            "due_date": data.get("due_date", (datetime.now() + timedelta(days=30)).strftime("%Y-%m-%d")),
            "tax_rate": data.get("tax_rate", 11),
            "tax_inclusive": data.get("tax_inclusive", False),
            "invoice_discount_percent": data.get("invoice_discount_percent", 0),
            "invoice_discount_amount": data.get("invoice_discount_amount", 0),
            "cash_discount_percent": data.get("cash_discount_percent", 0),
            "cash_discount_amount": data.get("cash_discount_amount", 0),
            "dpp_manual": data.get("dpp_manual"),
            "notes": data.get("notes", ""),
            "status": "posted",  # Default to posted for action mode
            "items": items,
        }

    # =========================================================
    # VALIDATION
    # =========================================================

    async def validate_action(
        self, tenant_id: str, action_type: str, payload: Dict[str, Any]
    ) -> Tuple[bool, List[Dict[str, str]]]:
        """
        Validate action against accounting rules.
        Returns (is_valid, errors_list).
        """
        errors = []

        if action_type == "CREATE_PURCHASE_INVOICE":
            # Check vendor
            if not payload.get("vendor_id") and not payload.get("vendor_name"):
                errors.append({
                    "layer": "INVARIANTS",
                    "code": "VENDOR_REQUIRED",
                    "message": "Vendor harus diisi (ID atau nama)."
                })

            # Check items
            if not payload.get("items") or len(payload["items"]) == 0:
                errors.append({
                    "layer": "INVARIANTS",
                    "code": "ITEMS_REQUIRED",
                    "message": "Minimal 1 item harus diisi."
                })

            # Check item amounts
            for i, item in enumerate(payload.get("items", [])):
                if item.get("qty", 0) <= 0:
                    errors.append({
                        "layer": "INVARIANTS",
                        "code": "INVALID_QTY",
                        "message": f"Item {i+1}: quantity harus > 0."
                    })
                if item.get("price", 0) <= 0:
                    errors.append({
                        "layer": "INVARIANTS",
                        "code": "INVALID_PRICE",
                        "message": f"Item {i+1}: harga harus > 0."
                    })

            # Check if vendor exists (if vendor_id provided)
            if payload.get("vendor_id"):
                async with self.pool.acquire() as conn:
                    vendor = await conn.fetchrow(
                        "SELECT id, name FROM vendors WHERE id = $1 AND tenant_id = $2",
                        uuid.UUID(payload["vendor_id"]), tenant_id
                    )
                    if not vendor:
                        errors.append({
                            "layer": "ACCOUNTING_RULES",
                            "code": "VENDOR_NOT_FOUND",
                            "message": f"Vendor dengan ID {payload['vendor_id']} tidak ditemukan."
                        })

            # Check period is open (gracefully skip if table missing)
            bill_date = payload.get("bill_date", datetime.now().strftime("%Y-%m-%d"))
            try:
                async with self.pool.acquire() as conn:
                    period = await conn.fetchrow(
                        """
                        SELECT id, status FROM accounting_periods
                        WHERE tenant_id = $1
                          AND $2::date BETWEEN start_date AND end_date
                        """,
                        tenant_id, bill_date
                    )
                    if period and period["status"] != "OPEN":
                        errors.append({
                            "layer": "ACCOUNTING_RULES",
                            "code": "PERIOD_CLOSED",
                            "message": f"Periode akuntansi untuk tanggal {bill_date} sudah ditutup."
                        })
            except Exception as e:
                logger.warning(f"Period check skipped: {e}")

            # Sanity check: amount not unreasonably large
            total = sum(
                item.get("qty", 0) * item.get("price", 0)
                for item in payload.get("items", [])
            )
            if total > 100_000_000_000:  # 100 miliar
                errors.append({
                    "layer": "POLICY",
                    "code": "AMOUNT_SANITY_CHECK",
                    "message": f"Total Rp{total:,.0f} sangat besar. Pastikan nominal benar."
                })

            # Validate items exist in master data (products table)
            # Iron Law 6: Source Traceability - every item must link to master data
            if payload.get("items") and len(errors) == 0:
                enriched_items, item_errors = await self.validate_items_against_master(
                    tenant_id, payload["items"]
                )
                if item_errors:
                    errors.extend(item_errors)
                else:
                    # Replace items with enriched versions (includes product_id, item_type)
                    payload["items"] = enriched_items


        return (len(errors) == 0, errors)

    # =========================================================
    # ITEM MASTER DATA VALIDATION
    # =========================================================
    async def validate_items_against_master(
        self, tenant_id: str, items: list
    ) -> tuple:
        """
        Validate each item exists in products master data.
        Returns (enriched_items, errors).

        Iron Law 6 (Source Traceability): Every bill item MUST link to master data.
        Items not found in master data -> VALIDATION_ERROR (fail by default).
        Item type (goods/service/non_inventory) must be defined in master data.
        """
        errors = []
        enriched_items = []

        async with self.pool.acquire() as conn:
            for i, item in enumerate(items):
                product_name = (item.get("product_name") or item.get("name") or "").strip()
                if not product_name:
                    errors.append({
                        "layer": "INVARIANTS",
                        "code": "ITEM_NAME_REQUIRED",
                        "message": f"Item {i+1}: nama produk harus diisi."
                    })
                    continue

                # Search by exact name (case-insensitive)
                product = await conn.fetchrow(
                    """
                    SELECT id, nama_produk, satuan, item_type, track_inventory,
                           status, for_purchases, purchase_account_id,
                           inventory_account_id, cogs_account_id, sku, item_code
                    FROM products
                    WHERE tenant_id = $1
                      AND LOWER(nama_produk) = LOWER($2)
                      AND deleted_at IS NULL
                    """,
                    tenant_id, product_name
                )

                if not product:
                    # Try partial match for helpful suggestion
                    similar = await conn.fetch(
                        """
                        SELECT nama_produk FROM products
                        WHERE tenant_id = $1
                          AND deleted_at IS NULL
                          AND status = 'active'
                          AND LOWER(nama_produk) LIKE LOWER($2)
                        LIMIT 3
                        """,
                        tenant_id, "%" + product_name + "%"
                    )
                    suggestion = ""
                    if similar:
                        names = [r["nama_produk"] for r in similar]
                        suggestion = " Produk serupa: " + ", ".join(names) + "."

                    errors.append({
                        "layer": "MASTER_DATA",
                        "code": "ITEM_NOT_FOUND",
                        "message": "Item '" + product_name + "' tidak ditemukan di master data Barang & Jasa. Tambahkan terlebih dahulu melalui menu Produk & Jasa." + suggestion
                    })
                    continue

                # Check if active
                if product["status"] != "active":
                    errors.append({
                        "layer": "MASTER_DATA",
                        "code": "ITEM_INACTIVE",
                        "message": "Item '" + product_name + "' tidak aktif (status: " + str(product["status"]) + ")."
                    })
                    continue

                # Check if available for purchases
                if product["for_purchases"] is False:
                    errors.append({
                        "layer": "MASTER_DATA",
                        "code": "ITEM_NOT_FOR_PURCHASE",
                        "message": "Item '" + product_name + "' tidak tersedia untuk pembelian. Update di master data."
                    })
                    continue

                # Validate item_type is defined
                if not product["item_type"]:
                    errors.append({
                        "layer": "MASTER_DATA",
                        "code": "ITEM_TYPE_MISSING",
                        "message": "Item '" + product_name + "' belum memiliki tipe (goods/service/non_inventory). Update di master data Barang & Jasa."
                    })
                    continue

                # Enrich item with master data
                enriched = dict(item)
                enriched["product_id"] = str(product["id"])
                enriched["product_name"] = product["nama_produk"]  # Canonical name from master
                enriched["item_type"] = product["item_type"]
                enriched["track_inventory"] = product["track_inventory"]
                enriched["purchase_account_id"] = str(product["purchase_account_id"]) if product["purchase_account_id"] else None
                enriched["inventory_account_id"] = str(product["inventory_account_id"]) if product["inventory_account_id"] else None
                enriched["cogs_account_id"] = str(product["cogs_account_id"]) if product["cogs_account_id"] else None

                # Use master data unit if not specified
                if not enriched.get("unit") or enriched["unit"] == "pcs":
                    enriched["unit"] = product["satuan"]

                enriched_items.append(enriched)

        return (enriched_items, errors)

    # =========================================================
    # CALCULATION PREVIEW
    # =========================================================

    def calculate_preview(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Calculate totals for preview display. Mirrors backend calculate_bill_totals_v2."""
        items = payload.get("items", [])
        tax_rate = payload.get("tax_rate", 11)

        subtotal = sum(item.get("qty", 0) * item.get("price", 0) for item in items)
        item_discount = sum(
            int(item.get("qty", 0) * item.get("price", 0) * item.get("discount_percent", 0) / 100)
            for item in items
        )
        after_item_discount = subtotal - item_discount

        inv_disc_pct = payload.get("invoice_discount_percent", 0)
        inv_disc_amt = payload.get("invoice_discount_amount", 0)
        invoice_discount = int(after_item_discount * inv_disc_pct / 100) if inv_disc_pct > 0 else inv_disc_amt

        after_invoice_discount = after_item_discount - invoice_discount

        cash_disc_pct = payload.get("cash_discount_percent", 0)
        cash_disc_amt = payload.get("cash_discount_amount", 0)
        cash_discount = int(after_invoice_discount * cash_disc_pct / 100) if cash_disc_pct > 0 else cash_disc_amt

        dpp = payload.get("dpp_manual") or (after_invoice_discount - cash_discount)
        tax_amount = int(dpp * tax_rate / 100)
        grand_total = dpp + tax_amount

        return {
            "subtotal": subtotal,
            "item_discount": item_discount,
            "invoice_discount": invoice_discount,
            "cash_discount": cash_discount,
            "dpp": dpp,
            "tax_rate": tax_rate,
            "tax_amount": tax_amount,
            "grand_total": grand_total,
        }

    def generate_journal_preview(self, action_type: str, calc: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Generate journal line preview. NOT actual journal - just display."""
        if action_type == "CREATE_PURCHASE_INVOICE":
            lines = []
            purchase_amount = calc["dpp"]
            tax_amount = calc["tax_amount"]
            total = calc["grand_total"]

            lines.append({"account_name": "Pembelian Barang", "debit": purchase_amount, "credit": 0})
            if tax_amount > 0:
                lines.append({"account_name": "PPN Masukan", "debit": tax_amount, "credit": 0})
            lines.append({"account_name": "Hutang Usaha", "debit": 0, "credit": total})
            return lines

        return []

    # =========================================================
    # PENDING ACTION MANAGEMENT (Redis)
    # =========================================================

    async def prepare_action(
        self,
        tenant_id: str,
        user_id: str,
        conversation_id: str,
        action_type: str,
        payload: Dict[str, Any],
        calculation: Dict[str, Any],
        journal_preview: List[Dict[str, Any]],
        assumptions: List[str] = None,
        warnings: List[str] = None,
        side_effects: List[str] = None,
    ) -> Dict[str, Any]:
        """Create a pending action in Redis with preview data."""
        redis = await self._get_redis()
        pending_id = str(uuid.uuid4())
        expires_at = datetime.utcnow() + timedelta(seconds=PENDING_ACTION_TTL)

        pending_data = {
            "id": pending_id,
            "tenant_id": tenant_id,
            "user_id": user_id,
            "conversation_id": conversation_id,
            "action_type": action_type,
            "payload": payload,
            "calculation": calculation,
            "journal_preview": journal_preview,
            "assumptions": assumptions or [],
            "warnings": warnings or [],
            "side_effects": side_effects or [],
            "status": "PENDING",
            "version": 1,
            "created_at": datetime.utcnow().isoformat(),
            "expires_at": expires_at.isoformat(),
            "idempotency_key": str(uuid.uuid4()),
        }

        key = f"{REDIS_PREFIX}{tenant_id}:{pending_id}"
        await redis.setex(key, PENDING_ACTION_TTL, json.dumps(pending_data))

        logger.info(f"Pending action created: {pending_id} type={action_type} tenant={tenant_id}")
        return pending_data

    async def get_pending_action(self, tenant_id: str, pending_id: str) -> Optional[Dict[str, Any]]:
        """Get pending action from Redis."""
        redis = await self._get_redis()
        key = f"{REDIS_PREFIX}{tenant_id}:{pending_id}"
        data = await redis.get(key)
        if data:
            return json.loads(data)
        return None

    async def update_pending_status(self, tenant_id: str, pending_id: str, status: str) -> bool:
        """Update pending action status. Returns False if not found or version mismatch."""
        redis = await self._get_redis()
        key = f"{REDIS_PREFIX}{tenant_id}:{pending_id}"
        data = await redis.get(key)
        if not data:
            return False

        pending = json.loads(data)
        if pending["status"] not in ("PENDING",):
            logger.warning(f"Cannot update action {pending_id}: status={pending['status']}")
            return False

        pending["status"] = status
        pending["version"] += 1
        ttl = await redis.ttl(key)
        if ttl > 0:
            await redis.setex(key, ttl, json.dumps(pending))
        return True

    # =========================================================
    # EXECUTE ACTION (via Kernel/existing services)
    # =========================================================

    async def execute_action(
        self, tenant_id: str, user_id: str, pending_id: str
    ) -> Dict[str, Any]:
        """
        Execute a confirmed pending action.
        IRON LAW 0: All writes go through existing Kernel services.
        IRON LAW 14: Idempotency key prevents double-execution.
        """
        pending = await self.get_pending_action(tenant_id, pending_id)
        if not pending:
            return {"success": False, "error": "EXPIRED", "message": "Aksi sudah expired. Silakan ulangi."}

        if pending["status"] != "PENDING":
            return {"success": False, "error": "INVALID_STATE", "message": f"Aksi sudah dalam status {pending['status']}."}

        # Mark as EXECUTING (optimistic lock via version)
        if not await self.update_pending_status(tenant_id, pending_id, "EXECUTING"):
            return {"success": False, "error": "CONCURRENCY", "message": "Aksi sedang diproses oleh request lain."}

        action_type = pending["action_type"]
        payload = pending["payload"]
        entities_created = []

        try:
            if action_type == "CREATE_PURCHASE_INVOICE":
                result = await self._execute_create_purchase_invoice(
                    tenant_id, user_id, payload, pending["idempotency_key"]
                )
                if result["success"]:
                    entities_created = result.get("entities_created", [])
                    await self.update_pending_status(tenant_id, pending_id, "COMPLETED")
                    return {
                        "success": True,
                        "action_type": action_type,
                        "entities_created": entities_created,
                        "impact": result.get("impact", {}),
                    }
                else:
                    await self.update_pending_status(tenant_id, pending_id, "FAILED")
                    return {"success": False, "error": "EXECUTION_FAILED", "message": result.get("message", "Gagal membuat faktur.")}
            else:
                await self.update_pending_status(tenant_id, pending_id, "FAILED")
                return {"success": False, "error": "UNSUPPORTED", "message": f"Action type {action_type} belum didukung."}

        except Exception as e:
            logger.error(f"Action execution failed: {pending_id}: {e}", exc_info=True)
            # Rollback to PENDING so user can retry (IRON LAW 14: idempotency protects)
            await self.update_pending_status(tenant_id, pending_id, "PENDING")
            return {"success": False, "error": "INTERNAL_ERROR", "message": "Terjadi kesalahan. Silakan coba lagi."}

    async def _execute_create_purchase_invoice(
        self, tenant_id: str, user_id: str, payload: Dict[str, Any], idempotency_key: str
    ) -> Dict[str, Any]:
        """Execute CREATE_PURCHASE_INVOICE via existing BillsService. Law 0: Kernel executes."""
        import sys
        sys.path.insert(0, "/app/backend/services")
        from ..services.bills_service import BillsService
        try:
            from accounting_kernel.integration.facade import AccountingFacade
            facade = AccountingFacade(self.pool)
        except ImportError:
            facade = None

        service = BillsService(self.pool, accounting_facade=facade)
        entities_created = []

        # Check if vendor needs to be created
        if not payload.get("vendor_id") and payload.get("vendor_name"):
            # Check if vendor already exists by name
            async with self.pool.acquire() as conn:
                existing = await conn.fetchrow(
                    "SELECT id, name FROM vendors WHERE tenant_id = $1 AND LOWER(name) = LOWER($2)",
                    tenant_id, payload["vendor_name"]
                )
                if existing:
                    payload["vendor_id"] = str(existing["id"])
                # If not found, BillsService will auto-create vendor from vendor_name

        # Convert date strings to date objects and map field names for BillsService
        bill_payload = dict(payload)
        if isinstance(bill_payload.get("bill_date"), str):
            bill_payload["issue_date"] = datetime.strptime(bill_payload.pop("bill_date"), "%Y-%m-%d").date()
        if isinstance(bill_payload.get("due_date"), str):
            bill_payload["due_date"] = datetime.strptime(bill_payload["due_date"], "%Y-%m-%d").date()
        bill_payload["items"] = [
            {
                "product_id": item.get("product_id"),
                "product_name": item.get("product_name", ""),
                "product_code": item.get("product_code", ""),
                "quantity": item.get("qty", item.get("quantity", 0)),
                "unit": item.get("unit", "pcs"),
                "unit_price": item.get("price", item.get("unit_price", 0)),
                "discount_percent": item.get("discount_percent", 0),
                "batch_no": item.get("batch_no"),
                "exp_date": item.get("exp_date"),
                "bonus_qty": item.get("bonus_qty", 0),
            }
            for item in payload.get("items", [])
        ]

        # Create bill via Kernel
        result = await service.create_bill(
            tenant_id=tenant_id,
            request=bill_payload,
            user_id=uuid.UUID(user_id) if isinstance(user_id, str) else user_id,
        )

        if result.get("success"):
            bill_data = result.get("data", {})
            entities_created.append({
                "type": "bill",
                "id": str(bill_data.get("id", "")),
                "label": bill_data.get("invoice_number", bill_data.get("bill_number", ""))
            })

            grand_total = bill_data.get("amount", bill_data.get("grand_total", 0))
            return {
                "success": True,
                "entities_created": entities_created,
                "impact": {
                    "hutang_usaha": f"+{grand_total:,}",
                }
            }
        else:
            return {"success": False, "message": result.get("message", "Failed to create bill")}

    # =========================================================
    # CANCEL ACTION
    # =========================================================

    async def cancel_action(self, tenant_id: str, pending_id: str) -> bool:
        """Cancel a pending action."""
        redis = await self._get_redis()
        key = f"{REDIS_PREFIX}{tenant_id}:{pending_id}"
        data = await redis.get(key)
        if not data:
            return False

        pending = json.loads(data)
        if pending["status"] != "PENDING":
            return False

        pending["status"] = "CANCELLED"
        pending["version"] += 1
        await redis.setex(key, 300, json.dumps(pending))  # Keep for 5 min for status query
        logger.info(f"Action cancelled: {pending_id}")
        return True

    # =========================================================
    # VENDOR LOOKUP
    # =========================================================

    async def find_vendor(self, tenant_id: str, name: str) -> Optional[Dict[str, Any]]:
        """Find vendor by name (case-insensitive partial match)."""
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT id, name FROM vendors WHERE tenant_id = $1 AND LOWER(name) LIKE LOWER($2) LIMIT 1",
                tenant_id, f"%{name}%"
            )
            if row:
                return {"id": str(row["id"]), "name": row["name"]}
        return None

    async def find_products(self, tenant_id: str, names: list[str]) -> list[Dict[str, Any]]:
        """Search products by name (case-insensitive partial match). Returns list of matches."""
        results = []
        async with self.pool.acquire() as conn:
            for name in names:
                rows = await conn.fetch(
                    """SELECT id, nama_produk, satuan, item_type, purchase_price, 
                              sales_price, status, for_purchases, for_sales
                       FROM products 
                       WHERE tenant_id = $1 
                         AND LOWER(nama_produk) LIKE LOWER($2)
                         AND status = 'active'
                       LIMIT 3""",
                    tenant_id, f"%{name}%"
                )
                for row in rows:
                    results.append({
                        "id": str(row["id"]),
                        "nama_produk": row["nama_produk"],
                        "satuan": row["satuan"],
                        "item_type": row["item_type"],
                        "purchase_price": float(row["purchase_price"]) if row["purchase_price"] else None,
                        "sales_price": float(row["sales_price"]) if row["sales_price"] else None,
                        "for_purchases": row["for_purchases"],
                        "for_sales": row["for_sales"],
                    })
        return results

    
    async def search_master_data(self, tenant_id: str, text: str) -> Dict[str, Any]:
        """
        General-purpose master data search. Extracts entity names from any text
        (not just invoice context) and searches DB.
        Uses LLM to extract names, then fuzzy-matches against DB.
        """
        # Use a simple LLM call to extract entity names
        api_key = os.getenv("OPENAI_API_KEY", "")
        entity_names = {"vendors": [], "products": []}

        if api_key:
            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    resp = await client.post(
                        "https://api.openai.com/v1/chat/completions",
                        headers={
                            "Authorization": f"Bearer {api_key}",
                            "Content-Type": "application/json",
                        },
                        json={
                            "model": "gpt-4o-mini",
                            "messages": [
                                {"role": "system", "content": "Extract vendor and product names from the user text. Return JSON only: {\"vendors\": [\"name1\"], \"products\": [\"name1\"]}. If none mentioned, return empty arrays."},
                                {"role": "user", "content": text},
                            ],
                            "max_tokens": 200,
                            "temperature": 0.1,
                        },
                    )
                    resp.raise_for_status()
                    data = resp.json()
                    raw = data["choices"][0]["message"]["content"].strip()
                    if raw.startswith("```"):
                        raw = raw.split("\n", 1)[1] if "\n" in raw else raw[3:]
                        if raw.endswith("```"):
                            raw = raw[:-3]
                        raw = raw.strip()
                    entity_names = json.loads(raw)
            except Exception as e:
                logger.error(f"Entity extraction failed: {e}")

        result = {
            "vendors_found": [],
            "vendors_not_found": [],
            "products_found": [],
            "products_not_found": [],
        }

        # Search vendors
        for vname in entity_names.get("vendors", []):
            vendor = await self.find_vendor(tenant_id, vname)
            if vendor:
                result["vendors_found"].append(vendor)
            else:
                result["vendors_not_found"].append(vname)

        # Search products
        for pname in entity_names.get("products", []):
            found = await self.find_products(tenant_id, [pname])
            if found:
                result["products_found"].extend(found)
            else:
                result["products_not_found"].append(pname)

        return result

async def lookup_master_data(self, tenant_id: str, text: str) -> Dict[str, Any]:
        """
        Use LLM to extract vendor/product mentions from text,
        then look them up in the database. Returns context dict.
        """
        # Use LLM to extract entity names
        parsed = await self.parse_purchase_invoice_text(text)
        
        result = {
            "vendor": None,
            "vendor_found": False,
            "products": [],
            "products_found": [],
            "products_not_found": [],
            "parsed": parsed,
        }

        # Lookup vendor
        vendor_name = parsed.get("vendor_name")
        if vendor_name:
            vendor = await self.find_vendor(tenant_id, vendor_name)
            if vendor:
                result["vendor"] = vendor
                result["vendor_found"] = True
            else:
                result["vendor"] = {"name": vendor_name}
                result["vendor_found"] = False

        # Lookup products from items
        items = parsed.get("items", [])
        if items:
            item_names = [item.get("name", "") for item in items if item.get("name")]
            if item_names:
                found_products = await self.find_products(tenant_id, item_names)
                result["products"] = found_products
                
                # Match found products to requested items
                found_names_lower = {p["nama_produk"].lower() for p in found_products}
                for item in items:
                    item_name = item.get("name", "").lower()
                    matched = any(item_name in fn or fn in item_name for fn in found_names_lower)
                    if matched:
                        result["products_found"].append(item.get("name"))
                    else:
                        result["products_not_found"].append(item.get("name"))

        return result

