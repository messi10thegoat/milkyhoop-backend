"""
LLM Intent Router v4 — Optimized prompt (~700 tokens vs ~2300 in v3)

Single Gemini call: intent + entities + confidence + ready.
Phase 1: shadow. Phase 2: primary.
"""

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger("llm_intent_router")


@dataclass
class RouterOutput:
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


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Compact System Prompt (~700 tokens)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

ROUTER_SYSTEM_PROMPT = """Kamu intent router akuntansi Indonesia. Output JSON: intent, entities, confidence(0-1), ready(bool), slot_fill(obj|null), clarification(str|null), reasoning(str).

INTENT FORMAT — gunakan pattern berikut:

CRUD: {{action}}_{{module}}
  action: create, update, delete, void, reverse
  module: vendor, customer, item, warehouse, bank_account, account, sales_invoice, bill, expense, journal_entry, receive_payment, bill_payment, credit_note, vendor_credit, quote, bank_transfer, customer_deposit, vendor_deposit, stock_adjustment, work_order, bom, work_center

QUERY: query_{{module}}_{{type}}
  module: ar, ap, items, customers, vendors, bank, sales_invoices, bills, expenses, receive_payments, bill_payments, journals, accounts, credit_notes, vendor_credits, quotes, bank_transfers, customer_deposits, vendor_deposits, bom, work_order, production, work_center
  type: list, detail, summary, overdue, unpaid, search, aging, by_date, by_vendor
  Special: query_ar_outstanding, query_ap_outstanding, query_customer_ar, query_vendor_ap, query_cash_balance, query_customers_with_overdue, query_vendors_with_overdue, query_item_stock_card, query_items_low_stock, query_items_no_stock, query_warehouse_stock, query_inventory_summary, query_inventory_health, query_account_ledger, query_general_ledger, query_profit_loss, query_balance_sheet, query_cash_flow, query_trial_balance, query_dashboard_summary, query_overdue_all, query_top_expenses

CALC: calc_{{op}}_{{what}}
  "rata-rata harga jual"→calc_avg_harga_jual. "total harga beli"→calc_sum_harga_beli. "total stok"→calc_sum_stok.
  "berapa X aktif/inactive"→calc_count_X_active/inactive. "termahal/terbanyak"→calc_rank_items_by_price/stock.
  "total penjualan/pembelian/pengeluaran bulan ini"→calc_sum_sales/purchases/expenses_this_month.
  "total saldo semua rekening"→calc_sum_all_bank_balances. "total pembayaran masuk/keluar"→calc_sum_received/paid_this_month.
  "pelanggan piutang terbesar"→calc_rank_customers_by_ar. "vendor hutang terbesar"→calc_rank_vendors_by_ap.
  RULE: "total/jumlah" + angka = calc_sum. "terbesar/ranking" = calc_rank. "berapa banyak" = calc_count. BUKAN query.

OTHER: chitchat, ambiguous, reformat_as_table, contextual_drill_down

COMMON INTENTS (gunakan PERSIS):
create_vendor, create_customer, create_item, create_sales_invoice, create_sales_order, create_bill, create_expense, create_journal_entry, create_bank_account, create_warehouse, create_account, create_stock_adjustment, create_receive_payment, create_bill_payment, create_credit_note, create_vendor_credit, create_quote, create_bank_transfer, create_customer_deposit, create_vendor_deposit

SALES DOCUMENT DISTINCTION (CRITICAL — jangan tertukar):
- "buat/bikin pesanan [penjualan]" / "pesanan untuk X" / "bikin SO" / "sales order" → create_sales_order (komitmen, BELUM jurnal)
- "buat/bikin faktur [penjualan]" / "faktur untuk X" / "bikin invoice" / "tagih X" → create_sales_invoice (AR, ADA jurnal)
- "buat/bikin penawaran" / "bikin quote" / "quotation" / "tawaran harga" → create_quote (belum komitmen)
"pesanan" ≠ "faktur" ≠ "penawaran". Gunakan kata kunci user PERSIS: pesanan→SO, faktur→INV, penawaran→QUOTE.
update_vendor, update_customer, update_item, update_bank_account, update_warehouse
delete_vendor, delete_customer, delete_item, delete_warehouse, delete_bank_account
void_sales_invoice, void_bill, void_expense, void_bill_payment, void_receive_payment, reverse_journal
query_ar_outstanding, query_ap_outstanding, query_ar_invoices, query_ar_aging, query_ap_aging, query_customer_ar, query_vendor_ap, query_customers_with_overdue, query_vendors_with_overdue
query_items_search, query_item_detail, query_items_summary, query_items_low_stock, query_items_no_stock, query_warehouse_stock
query_sales_invoices_list, query_sales_invoice_detail, query_sales_invoices_summary, query_sales_invoices_unpaid, query_sales_invoices_overdue
query_bills_list, query_bill_detail, query_bills_summary, query_bills_unpaid, query_bills_overdue
query_expenses_list, query_expense_detail, query_expenses_summary
query_customers_list, query_customer_detail, query_vendors_list, query_vendor_detail
query_bank_accounts_list, query_bank_account_balance, query_bank_transactions
query_accounts_list, query_account_ledger, query_profit_loss, query_balance_sheet, query_trial_balance, query_cash_balance
query_receive_payments_list, query_bill_payments_list, query_categories_list
query_warehouses, query_items_inactive, query_items_slow_moving
calc_avg_harga_jual, calc_sum_harga_beli, calc_sum_stok, calc_sum_all_bank_balances
calc_count_items_active, calc_count_customers_active, calc_count_vendors_active
calc_rank_items_by_price, calc_rank_items_by_stock, calc_rank_customers_by_ar, calc_rank_vendors_by_ap, calc_rank_expense_accounts
calc_sum_sales_this_month, calc_sum_purchases_this_month, calc_sum_expenses_this_month
calc_sum_received_this_month, calc_sum_paid_this_month

MANUFACTURING INTENTS:
create_work_order, create_bom, create_work_center
release_work_order, start_work_order, complete_work_order, void_work_order, cancel_work_order
issue_materials, report_production_output
query_bom_list, query_bom_detail, query_bom_cost_breakdown, query_bom_materials_required
query_work_order_list, query_work_order_detail, query_work_order_cost_analysis
query_production_active, query_production_schedule, query_material_issues, query_fg_receipts
query_work_center_list
calc_count_work_orders_active, calc_count_bom_active, calc_count_work_orders_draft, calc_count_work_centers, calc_rank_work_orders_by_quantity

RULES:
1. "daftar/list/semua/lihat/ringkasan/detail/cari" = QUERY. "buat/tambah/bikin/catat/daftarkan/input" = CREATE.
   "daftar barang/produk" → query_items_search. "ringkasan barang" → query_items_summary. "detail X" → query_X_detail.
2. "hutang/utang" tanpa vendor → query_ap_outstanding. "hutang ke X" → query_vendor_ap. "piutang" tanpa pelanggan → query_ar_outstanding. "piutang X" → query_customer_ar. "piutang siapa saja" → query_ar_invoices. "aging" → query_ar/ap_aging.
3. "hapus vendor X"→delete_vendor. "hapus barang X"→delete_item. Entity keyword setelah "hapus" = type.
4. "catat biaya/beban" → create_expense. "catat pembelian dari vendor" → create_bill. Tanpa vendor = expense.
5. "rekening bank/BCA" → create_bank_account. "akun beban/pendapatan" → create_account. "ke BCA" = bank_name.
6. Untuk create_expense: jika user sebut nama akun biaya (contoh: "beban pemeliharaan", "beban listrik", "biaya admin"), extract sebagai account_name. JANGAN masukkan ke description.
6. "penyesuaian/koreksi stok" → create_stock_adjustment.
7. Angka: "5 juta"→5000000, "500rb"→500000. READY=true jika required fields lengkap.
8. Chitchat: greeting/thanks/identity → chitchat. Single number/ambigu tanpa workflow → chitchat.
9. Workflow aktif: jawaban apapun (bukan "batal/cancel") = slot_fill intent workflow, BUKAN intent baru.
10. "overdue/jatuh tempo pelanggan" → query_customers_with_overdue. "overdue vendor" → query_vendors_with_overdue.
11. Clarification max 3 field sekaligus. Prioritas: entity utama → item → qty+harga.
12. Single word tanpa workflow: nama barang → query_item_detail. Nama orang → query_customer_detail.
13. MULTI-TURN: Jika riwayat percakapan menunjukkan ACTIVE CREATE/UPDATE workflow, jawaban pendek
    (nama, angka, 1-2 kata) hampir PASTI slot_fill untuk workflow tersebut, BUKAN intent baru.
    Contoh: history="buat faktur" → "poloshirt 20 pcs" = slot_fill create_sales_invoice.
    Contoh: history="catat biaya" → "listrik 450rb" = slot_fill create_expense.
    Contoh: history="bayar tagihan" → "BCA" = slot_fill create_bill_payment (BUKAN query_bank).
14. "ringkasan/total pengeluaran bulan ini" → calc_sum_expenses_this_month (BUKAN query_expenses_summary).
15. "arus kas" → query_cash_flow. "neraca saldo" → query_trial_balance. "neraca" → query_balance_sheet. "laba rugi" → query_profit_loss.
    "ringkasan X" tanpa "bulan ini/total" → query_X_summary.
16. DOMAIN CONTINUITY (CRITICAL): Pesan pendek (<6 kata) atau pronoun TANPA keyword domain eksplisit → TETAP di domain RIWAYAT sebelumnya.
    Setelah hutang/AP: "ke siapa aja?" → query_ap_outstanding. "ke vendor siapa aja?" → query_ap_outstanding. "yang paling besar?" → calc_rank_vendors_by_ap. "bayar yang paling besar" → create_bill_payment.
    Setelah piutang/AR: "ke siapa aja?" → query_ar_invoices. "dari siapa aja?" → query_ar_invoices. "yang paling besar?" → calc_rank_customers_by_ar. "yang paling besar siapa?" → calc_rank_customers_by_ar.
    Setelah barang: "yang paling mahal?" → calc_rank_items_by_price. "yang stoknya habis?" → query_items_no_stock.
    DILARANG: switch domain, minta klarifikasi, atau jawab "sebutkan nama" untuk query pendek yang jelas merujuk history.
17. REFORMAT: "tampilkan dalam tabel/tabelkan/bikin tabel/rekapan tabel" → SELALU reformat_as_table. Confidence 1.0. Tidak perlu tanya "data apa?".
18. ENTITY EXTRACTION WAJIB: Jika user sebut NAMA di query, SELALU extract:
    "piutang [nama]" → query_customer_ar, customer_name=[nama]. "hutang ke [nama]" → query_vendor_ap, vendor_name=[nama].
    "cek stok [nama]" / "stok [nama] berapa" → query_item_detail, item_name=[nama].
    "saldo [bank]" → query_bank_account_balance, bank_name=[bank].
    DILARANG jawab "sebutkan nama" jika nama SUDAH ada di pesan. Bahkan setelah CRUD/daftar context.
19. STANDALONE STOCK: "stok habis/kosong/nol/0" tanpa context barang sebelumnya → query_items_no_stock. "stok rendah/sedikit" → query_items_low_stock.
20. MANUFACTURING: "daftar/list BOM" → query_bom_list. "detail BOM X" → query_bom_detail. "daftar work order/WO" → query_work_order_list. "WO aktif" → query_production_active. "buat work order" → create_work_order. "release/start/complete WO" → release/start/complete_work_order. "issue material" → issue_materials. "report output" → report_production_output. "jadwal produksi" → query_production_schedule. "biaya produksi" → query_work_order_cost_analysis. "daftar work center" → query_work_center_list.
    "rekening/bank paling banyak/terbesar" → query_bank_accounts_list (bukan agent loop).
21. MORE MAPPINGS (fast-path, hindari agent loop):
    "daftar gudang" / "list gudang" / "warehouse" / "semua gudang" → query_warehouses.
    "pengeluaran terbesar" / "biaya terbesar" / "top expenses" / "akun beban terbesar" → calc_rank_expense_accounts.
    "barang tidak aktif" / "item nonaktif" / "produk inactive" / "barang nonaktif" → query_items_inactive.
    "barang slow moving" / "slow moving" / "barang tidak laku" / "barang lama" / "dead stock" → query_items_slow_moving.
22. PAYMENT SUBJECT-AWARE (CRITICAL): tentukan SUBJEK kalimat untuk pilih intent payment.
    CUSTOMER bayar kita → create_receive_payment:
      "<NAMA> bayar X ke <bank>" → create_receive_payment, customer_name=<NAMA>.
      "<NAMA> transfer X" → create_receive_payment.
      "<NAMA> melunasi X" → create_receive_payment.
      "terima pembayaran/transfer dari <NAMA>" → create_receive_payment.
      "pembayaran/setoran masuk dari <NAMA>" → create_receive_payment.
    KITA bayar vendor → create_bill_payment:
      "bayar tagihan/vendor/supplier <NAMA>" → create_bill_payment, vendor_name=<NAMA>.
      "bayar ke <NAMA-vendor>" → create_bill_payment.
      "lunasi tagihan <NAMA>" → create_bill_payment.
      "bayar PB-<nomor>" → create_bill_payment.
    KITA bayar utility → create_expense (HANYA tanpa kata "tagihan"):
      "bayar PLN/PDAM/internet/listrik X" (tanpa "tagihan") → create_expense.
      "bayar tagihan PLN/PDAM/listrik X"                    → create_bill_payment (vendor utility).
    Contoh: "Maju Jaya bayar 5 juta ke BCA" → create_receive_payment. "bayar PT Sumber 3 juta" → create_bill_payment. "bayar PLN 450rb" → create_expense. "bayar tagihan PLN 450rb" → create_bill_payment.
20. ENTITY FROM HISTORY (CRITICAL): Jika user refer entity dari respons BOT sebelumnya, extract NAMA LENGKAP dari riwayat, BUKAN dari user text saja.
    "poloshirt harganya?" setelah bot sebut "Poloshirt Hitam + Bordir (42 pcs)" → item_name="Poloshirt Hitam + Bordir".
    "detail Sintia" setelah bot sebut "Sintia Runtuwene (Rp 175.000)" → customer_name="Sintia Runtuwene".
    "hutang ke Knitto?" setelah bot sebut "Knitto Textile Holis" → vendor_name="Knitto Textile Holis".
    "harganya?" / "stoknya?" / "piutangnya?" / "hutangnya?" → ambil entity dari riwayat terdekat.
    Juga berlaku untuk pronoun: "mereka"/"dia"/"di situ" → resolve ke entity dari riwayat.
    DILARANG jawab "sebutkan nama" jika entity bisa di-resolve dari riwayat."""


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
                    "description": "Only include fields that user explicitly mentioned. Keys: customer_name, vendor_name, item_name, bank_name, warehouse_name, name, invoice_number, bill_number, account_name, amount, quantity, unit_price, description, date, due_date, phone, email, address, reason, payment_method, item_type, base_unit",
                },
                "confidence": {"type": "number"},
                "ready": {"type": "boolean"},
                "slot_fill": {"type": ["object", "null"], "additionalProperties": True},
                "clarification": {"type": ["string", "null"]},
                "reasoning": {"type": "string"},
            },
            "required": [
                "intent",
                "entities",
                "confidence",
                "ready",
                "slot_fill",
                "clarification",
                "reasoning",
            ],
            "additionalProperties": False,
        },
    },
}


class LLMIntentRouter:
    """LLM-based intent router. Phase 1: shadow. Phase 2: primary."""

    def __init__(self, llm_router):
        self.llm_router = llm_router

    async def route(
        self,
        user_text: str,
        conversation_history: Optional[List[Dict[str, str]]] = None,
        workflow_state: Optional[Dict[str, Any]] = None,
        entity_memory: Optional[Dict[str, Any]] = None,
        ocr_text: Optional[str] = None,
    ) -> RouterOutput:
        from ..llm.llm_router import LLMMessage

        start = time.time()
        parts = []

        if conversation_history:
            recent = conversation_history[-10:]
            history_text = "\n".join(
                f"{'User' if m.get('role') == 'user' else 'Bot'}: {m.get('content', '')[:200 if m.get('role') == 'user' else 400]}"
                for m in recent
            )
            parts.append(f"== RIWAYAT ==\n{history_text}")

        if workflow_state:
            wf_intent = workflow_state.get("intent", "")
            parts.append(
                f"== WORKFLOW AKTIF: {wf_intent} ==\n"
                f"User menjawab pertanyaan workflow → slot_fill, BUKAN intent baru."
            )

        # Inject REC session state (P1.1)
        if (
            entity_memory
            and isinstance(entity_memory, dict)
            and "last_domain" in entity_memory
        ):
            _session_parts = []
            if entity_memory.get("last_domain"):
                _session_parts.append(
                    f"Domain terakhir: {entity_memory['last_domain']}"
                )
            if entity_memory.get("active_entity"):
                _ent = entity_memory["active_entity"]
                _session_parts.append(
                    f"Entitas aktif: {_ent.get('name', '?')} ({_ent.get('type', '?')})"
                )
            if entity_memory.get("last_numeric"):
                _num = entity_memory["last_numeric"]
                if _num.get("total") is not None:
                    _session_parts.append(f"Angka terakhir: {_num['total']}")
            if entity_memory.get("last_response_items"):
                _names = [
                    i.get("name", "?") for i in entity_memory["last_response_items"][:3]
                ]
                _session_parts.append(f"Item terakhir: {', '.join(_names)}")
            if _session_parts:
                parts.append("== SESSION STATE ==\n" + "\n".join(_session_parts))
            entity_memory = None  # Consumed - don't also process as old-format memory

        if entity_memory:
            mem = [
                f"{k}: {v['data'].get('name','?')}"
                for k, v in entity_memory.items()
                if isinstance(v, dict) and v.get("data")
            ]
            if mem:
                parts.append("== MEMORY ==\n" + "\n".join(mem))

        if ocr_text:
            parts.append(f"== OCR ==\n{ocr_text[:500]}")

        parts.append(f"== PESAN ==\n{user_text}")

        messages = [
            LLMMessage(role="system", content=ROUTER_SYSTEM_PROMPT),
            LLMMessage(role="user", content="\n\n".join(parts)),
        ]

        try:
            response = await self.llm_router.complete(
                task_type="shadow_router",
                messages=messages,
                temperature=0.1,
                max_tokens=300,
                response_format=ROUTER_RESPONSE_SCHEMA,
            )

            latency = int((time.time() - start) * 1000)
            raw = {}
            if response and response.content:
                try:
                    raw = json.loads(response.content)
                except json.JSONDecodeError:
                    logger.warning(
                        "[LLM_ROUTER] JSON parse failed: %s", response.content[:200]
                    )
                    return RouterOutput(
                        intent="FALLBACK",
                        confidence=0.0,
                        reasoning="JSON parse error",
                        latency_ms=latency,
                    )

            entities = {
                k: v for k, v in (raw.get("entities") or {}).items() if v is not None
            }

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
