"""
LLM Intent Router — Phase 1 (Shadow Mode)

Single Gemini call replaces regex classifiers + guards.
Returns structured intent + entities + confidence + slot_fill awareness.

Phase 1: runs in parallel (shadow), logs comparison, zero impact.
Phase 2: becomes primary with feature flag.
"""

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger("llm_intent_router")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Output Schema
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@dataclass
class RouterOutput:
    """Structured output from LLM Router."""
    intent: str = "ambiguous"
    entities: Dict[str, Any] = field(default_factory=dict)
    confidence: float = 0.0
    ready: bool = False
    slot_fill: Optional[Dict[str, Any]] = None
    clarification: Optional[str] = None
    reasoning: str = ""
    raw_response: Dict[str, Any] = field(default_factory=dict)
    latency_ms: int = 0
    input_tokens: int = 0
    output_tokens: int = 0


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Grouped Intent Categories (compact for prompt, ~500 tokens)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

INTENT_GROUPS = """
## CRUD Master Data
create_customer, create_vendor, create_item, create_warehouse, create_bank_account, create_category, create_unit, create_account
update_customer, update_vendor, update_item, update_warehouse, update_bank_account, update_account
delete_customer, delete_vendor, delete_item, delete_warehouse, delete_bank_account

## Transaksi Penjualan
create_sales_invoice, create_receive_payment, create_credit_note, create_quote, create_customer_deposit
void_sales_invoice, void_receive_payment, void_credit_note, void_customer_deposit

## Transaksi Pembelian
create_bill, create_bill_payment, create_vendor_credit, create_vendor_deposit
void_bill, void_bill_payment, void_vendor_credit, void_vendor_deposit

## Beban & Bank
create_expense, create_journal_entry, create_bank_transfer, create_stock_adjustment
void_expense, reverse_journal, void_bank_transfer, void_stock_adjustment

## Query Piutang/Hutang
query_ar_outstanding, query_ar_invoices, query_ar_aging, query_customer_ar
query_ap_outstanding, query_ap_aging, query_vendor_ap

## Query Kas & Bank
query_cash_balance, query_bank_accounts_list, query_bank_account_detail, query_bank_account_balance, query_bank_transactions, query_bank_transactions_by_date, query_bank_transfers_list

## Query Faktur & Pembayaran
query_sales_invoices_list, query_sales_invoice_detail, query_sales_invoices_summary, query_sales_invoices_overdue, query_sales_invoices_unpaid
query_bills_list, query_bill_detail, query_bills_summary, query_bills_overdue, query_bills_unpaid, query_bills_by_vendor
query_receive_payments_list, query_receive_payment_detail, query_bill_payments_list, query_bill_payment_detail

## Query Beban
query_expenses_list, query_expense_detail, query_expenses_summary, query_expenses_by_account, query_expenses_by_date_range, query_top_expenses

## Query Barang & Stok
query_item_detail, query_item_stock_card, query_item_transactions, query_items_summary, query_items_low_stock
query_items_top_products, query_items_slow_moving, query_items_margins, query_items_search, query_items_by_stock
query_items_inactive, query_items_no_stock, query_items_stats, query_items_units, query_items_by_price
query_warehouse_stock, query_warehouse_stock_value, query_warehouses, query_inventory_summary, query_inventory_health
query_stock_adjustments, query_stock_adjustments_summary, query_stock_transfers, query_stock_in_transit

## Query Pelanggan & Vendor
query_customer_detail, query_customers_list, query_customers_summary, query_customers_with_overdue
query_vendor_detail, query_vendors_list, query_vendors_summary, query_vendors_with_overdue

## Query Jurnal & Akun
query_journals_list, query_journal_detail, query_accounts_list, query_account_detail, query_account_ledger, query_general_ledger

## Query Laporan
query_profit_loss, query_balance_sheet, query_cash_flow, query_trial_balance, query_dashboard_summary, query_overdue_all

## Kalkulasi
calc_avg_harga_jual, calc_sum_harga_jual, calc_avg_harga_beli, calc_sum_harga_beli
calc_sum_stok, calc_count_items_active, calc_count_items_inactive
calc_rank_items_by_price, calc_rank_items_by_stock
calc_count_customers_active, calc_count_vendors_active, calc_count_customers_inactive, calc_count_vendors_inactive
calc_rank_customers_by_ar, calc_rank_vendors_by_ap
calc_sum_sales_this_month, calc_sum_purchases_this_month, calc_sum_expenses_this_month
calc_sum_received_this_month, calc_sum_paid_this_month, calc_sum_all_bank_balances
calc_count_sales_invoices_active, calc_count_bills_active, calc_count_expenses_this_month
calc_rank_expense_accounts

## Special
chitchat, ambiguous, reformat_as_table, contextual_drill_down
"""


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# System Prompt
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

ROUTER_SYSTEM_PROMPT = """Kamu adalah intent router untuk sistem akuntansi Indonesia (MilkyHoop).

TUGAS: Tentukan intent, extract entities, dan tentukan apakah data sudah cukup untuk eksekusi.

OUTPUT: JSON object dengan field:
- intent: string — pilih dari daftar intent di bawah
- entities: object — entity yang di-extract dari pesan user
- confidence: float 0.0-1.0
- ready: boolean — true jika semua required field sudah ada untuk langsung propose
- slot_fill: object|null — jika user menjawab pertanyaan workflow (isi field yang dijawab)
- clarification: string|null — pertanyaan follow-up jika data kurang (max 3 field sekaligus)
- reasoning: string — 1 kalimat alasan keputusan

DAFTAR INTENT:
{intent_groups}

ENTITY FIELDS:
customer_name, vendor_name, item_name, bank_name, warehouse_name, name,
invoice_number, bill_number, amount, quantity, unit_price,
description, date, due_date, phone, email, address, reason,
account_type, payment_method, item_type, base_unit,
items (array of {{name, qty, price}})

RULES:
1. KONTEKS: Jika workflow aktif dan user menjawab pertanyaan, itu SLOT FILL — bukan intent baru.
   Contoh: Bot tanya "pelanggan siapa?" → user: "sintia" → slot_fill: {{"customer_name": "Sintia"}}
2. MULTI-KEYWORD: "daftarkan vendor dengan rekening BCA" = create_vendor.
   Kata "vendor/pelanggan/item" di awal = entity utama. "rekening/bank" di belakang = sub-field.
3. ANGKA: "5 juta"→5000000, "500rb"→500000, "2,5jt"→2500000.
4. BANK: "ke BCA/Mandiri/BRI" = bank_name, BUKAN vendor_name.
5. READY=true jika semua required field ada. create_vendor+name, create_customer+name, create_item+name, create_expense+amount+description, create_sales_invoice+customer+items.
6. CLARIFICATION max 3 fields sekaligus. Prioritas: entity utama → item → qty+harga.
7. CONFIDENCE: 1.0=jelas, 0.7=konteks membantu, 0.5=ambigu, 0.3=tidak yakin.
8. CHITCHAT: greeting/thanks/identity → "chitchat", confidence 1.0.
9. VOID hanya jika user EKSPLISIT bilang void/batal/batalkan. DELETE hanya untuk master data (vendor/customer/item/warehouse/bank_account). Transaksi TIDAK bisa di-delete, hanya void.
10. ITEM_TYPE: "barang"=goods, "jasa"=service. Jangan tebak dari nama.
11. "dia/tersebut/yang tadi" → gunakan entity memory jika ada.

== QUERY vs CREATE ==
12. "daftar/list/semua/lihat" + entity = QUERY, bukan CREATE:
    "daftar barang"→query_items_search. "daftar vendor"→query_vendors_list. "daftar pelanggan"→query_customers_list.
    "daftar faktur penjualan"→query_sales_invoices_list. "daftar tagihan"→query_bills_list.
    "ringkasan X"→query_X_summary. "detail X"→query_X_detail. "cari X"→query_X_search.
    Hanya "buat/tambah/bikin/catat/daftarkan/input" yang CREATE.

== CALC vs QUERY ==
13. Aggregasi numerik = calc_*, bukan query_*:
    "rata-rata harga"→calc_avg_harga_jual. "total harga beli"→calc_sum_harga_beli. "total stok"→calc_sum_stok.
    "berapa jumlah/banyak X aktif"→calc_count_X_active. "berapa X inactive"→calc_count_X_inactive.
    "barang termahal"→calc_rank_items_by_price. "stok terbanyak"→calc_rank_items_by_stock.
    "total penjualan bulan ini"→calc_sum_sales_this_month. "total pembelian bulan ini"→calc_sum_purchases_this_month.
    "total pengeluaran bulan ini"→calc_sum_expenses_this_month. "total saldo semua rekening"→calc_sum_all_bank_balances.
    "pelanggan piutang terbesar"→calc_rank_customers_by_ar. "vendor hutang terbesar"→calc_rank_vendors_by_ap.
    "total pembayaran masuk bulan ini"→calc_sum_received_this_month. "total pembayaran keluar bulan ini"→calc_sum_paid_this_month.
    CONTOH SALAH: "total saldo semua rekening"→query_bank_accounts_list (SALAH! harusnya calc_sum_all_bank_balances).
    CONTOH SALAH: "vendor hutang terbesar"→query_vendors_with_overdue (SALAH! harusnya calc_rank_vendors_by_ap).
    RULE: kata "total/jumlah/semua" + angka = calc_sum_*. Kata "terbesar/terkecil/ranking" = calc_rank_*.

== AR/AP ==
14. "hutang/utang" TANPA nama vendor → query_ap_outstanding. "hutang ke [vendor]" → query_vendor_ap.
    "piutang" TANPA nama pelanggan → query_ar_outstanding. "piutang [pelanggan]" → query_customer_ar.
    "siapa yang punya piutang" / "piutang siapa saja" → query_ar_invoices.
    "aging piutang" → query_ar_aging. "aging hutang" → query_ap_aging.
    "pelanggan piutangnya overdue" → query_customers_with_overdue. "vendor hutangnya overdue" → query_vendors_with_overdue.
    "faktur belum dibayar/lunas" → query_sales_invoices_unpaid. "tagihan belum lunas" → query_bills_unpaid.

== HAPUS/DELETE ==
15. Entity keyword setelah "hapus" menentukan type:
    "hapus vendor X"→delete_vendor. "hapus barang X"→delete_item. "hapus pelanggan X"→delete_customer.
    "hapus gudang X"→delete_warehouse. "hapus rekening X"→delete_bank_account.
    JANGAN campur: "hapus barang X" = delete_item, BUKAN delete_vendor walau X kebetulan juga nama vendor.

== EXPENSE vs BILL ==
16. "catat biaya/beban/pengeluaran + deskripsi" → create_expense. "catat pembelian dari vendor X" → create_bill.
    "biaya listrik/transport/makan" → create_expense. "beli barang dari X" → create_bill.
    Tanpa vendor = expense. Dengan vendor + barang = bill.

== AKUN vs REKENING ==
17. "buat akun/account baru" (Chart of Accounts) → create_account. "buat/tambah rekening bank" → create_bank_account.
    "akun beban/pendapatan/aset" → create_account. "rekening BCA/Mandiri" → create_bank_account.

== STOCK ADJUSTMENT ==
18. "penyesuaian stok/adjust stok/koreksi stok" → create_stock_adjustment.
    "tambah stok X" (tanpa "buat barang") → create_stock_adjustment, bukan create_item.

== SINGLE WORD / SHORT INPUT ==
19. Tanpa workflow aktif, single word input:
    Nama barang ("poloshirt") → query_item_detail. Nama orang ("sintia") → query_customer_detail.
    Number saja ("100000") → chitchat. Kata ambigu ("berapa") → chitchat."""


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Response Schema (for Gemini structured output)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

ROUTER_RESPONSE_SCHEMA = {
    "type": "json_schema",
    "json_schema": {
        "name": "router_output",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "intent": {"type": "string"},
                "entities": {
                    "type": "object",
                    "properties": {
                        "customer_name": {"type": ["string", "null"]},
                        "vendor_name": {"type": ["string", "null"]},
                        "item_name": {"type": ["string", "null"]},
                        "bank_name": {"type": ["string", "null"]},
                        "warehouse_name": {"type": ["string", "null"]},
                        "name": {"type": ["string", "null"]},
                        "invoice_number": {"type": ["string", "null"]},
                        "bill_number": {"type": ["string", "null"]},
                        "amount": {"type": ["number", "null"]},
                        "quantity": {"type": ["number", "null"]},
                        "unit_price": {"type": ["number", "null"]},
                        "description": {"type": ["string", "null"]},
                        "date": {"type": ["string", "null"]},
                        "due_date": {"type": ["string", "null"]},
                        "phone": {"type": ["string", "null"]},
                        "email": {"type": ["string", "null"]},
                        "address": {"type": ["string", "null"]},
                        "reason": {"type": ["string", "null"]},
                        "payment_method": {"type": ["string", "null"]},
                        "item_type": {"type": ["string", "null"]},
                        "base_unit": {"type": ["string", "null"]},
                    },
                    "additionalProperties": False,
                    "required": [
                        "customer_name", "vendor_name", "item_name", "bank_name",
                        "warehouse_name", "name", "invoice_number", "bill_number",
                        "amount", "quantity", "unit_price", "description", "date",
                        "due_date", "phone", "email", "address", "reason",
                        "payment_method", "item_type", "base_unit",
                    ],
                },
                "confidence": {"type": "number"},
                "ready": {"type": "boolean"},
                "slot_fill": {
                    "type": ["object", "null"],
                    "additionalProperties": True,
                },
                "clarification": {"type": ["string", "null"]},
                "reasoning": {"type": "string"},
            },
            "required": ["intent", "entities", "confidence", "ready", "slot_fill", "clarification", "reasoning"],
            "additionalProperties": False,
        },
    },
}


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Router Class
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class LLMIntentRouter:
    """LLM-based intent router. Phase 1: shadow. Phase 2: primary."""

    def __init__(self, llm_router):
        """
        Args:
            llm_router: LLMRouter instance (from llm_router.py) for Gemini calls.
        """
        self.llm_router = llm_router

    async def route(
        self,
        user_text: str,
        conversation_history: Optional[List[Dict[str, str]]] = None,
        workflow_state: Optional[Dict[str, Any]] = None,
        entity_memory: Optional[Dict[str, Any]] = None,
        ocr_text: Optional[str] = None,
    ) -> RouterOutput:
        """
        Single LLM call to classify intent + extract entities.

        Args:
            user_text: User's message
            conversation_history: Last 5 turns [{role, content}, ...]
            workflow_state: Active workflow info {intent, phase, missing_fields}
            entity_memory: Recent entity context {customer: {name, id}, ...}
            ocr_text: Extracted text from image (if any)
        """
        from ..llm.llm_router import LLMMessage

        start = time.time()

        # Build system prompt
        system = ROUTER_SYSTEM_PROMPT.format(intent_groups=INTENT_GROUPS)

        # Build user message with context
        parts = []

        # Conversation history (last 5 turns)
        if conversation_history:
            recent = conversation_history[-10:]  # 5 pairs
            history_text = "\n".join(
                f"{'User' if m.get('role') == 'user' else 'Bot'}: {m.get('content', '')[:200]}"
                for m in recent
            )
            parts.append(f"== RIWAYAT PERCAKAPAN (terakhir) ==\n{history_text}")

        # Active workflow
        if workflow_state:
            wf_intent = workflow_state.get("intent", "")
            wf_missing = workflow_state.get("missing_fields", [])
            parts.append(
                f"== WORKFLOW AKTIF ==\n"
                f"Intent: {wf_intent}\n"
                f"Field yang masih kosong: {', '.join(wf_missing) if wf_missing else 'tidak ada'}\n"
                f"PENTING: Jika user menjawab pertanyaan workflow, isi slot_fill. Jangan buat intent baru."
            )

        # Entity memory
        if entity_memory:
            mem_lines = []
            for etype, edata in entity_memory.items():
                if isinstance(edata, dict) and edata.get("data"):
                    name = edata["data"].get("name", "?")
                    mem_lines.append(f"{etype}: {name}")
            if mem_lines:
                parts.append(
                    f"== ENTITY MEMORY ==\n"
                    + "\n".join(mem_lines)
                    + "\nGunakan jika user bilang 'dia', 'tersebut', 'yang tadi'. "
                    "Jika user sebut nama BARU → itu koreksi, OVERWRITE."
                )

        # OCR text
        if ocr_text:
            parts.append(f"== TEKS DARI GAMBAR (OCR) ==\n{ocr_text[:500]}")

        # Current message
        parts.append(f"== PESAN USER ==\n{user_text}")

        user_content = "\n\n".join(parts)

        # Call Gemini via LLMRouter
        messages = [
            LLMMessage(role="system", content=system),
            LLMMessage(role="user", content=user_content),
        ]

        try:
            response = await self.llm_router.complete(
                task_type="shadow_router",
                messages=messages,
                temperature=0.1,
                max_tokens=500,
                response_format=ROUTER_RESPONSE_SCHEMA,
            )

            latency = int((time.time() - start) * 1000)

            # Parse response
            raw = {}
            if response and response.content:
                try:
                    raw = json.loads(response.content)
                except json.JSONDecodeError:
                    logger.warning("[LLM_ROUTER] JSON parse failed: %s", response.content[:200])
                    return RouterOutput(
                        intent="FALLBACK",
                        confidence=0.0,
                        reasoning="JSON parse error",
                        latency_ms=latency,
                    )

            # Filter null entities
            entities = {}
            for k, v in (raw.get("entities") or {}).items():
                if v is not None:
                    entities[k] = v

            return RouterOutput(
                intent=raw.get("intent", "ambiguous"),
                entities=entities,
                confidence=raw.get("confidence", 0.0),
                ready=raw.get("ready", False),
                slot_fill=raw.get("slot_fill"),
                clarification=raw.get("clarification"),
                reasoning=raw.get("reasoning", ""),
                raw_response=raw,
                latency_ms=latency,
                input_tokens=getattr(response, "input_tokens", 0) or 0,
                output_tokens=getattr(response, "output_tokens", 0) or 0,
            )

        except Exception as e:
            latency = int((time.time() - start) * 1000)
            logger.warning("[LLM_ROUTER] Call failed (%dms): %s", latency, e)
            return RouterOutput(
                intent="FALLBACK",
                confidence=0.0,
                reasoning=f"LLM error: {str(e)[:100]}",
                latency_ms=latency,
            )
