import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


def _format_rupiah(amount) -> str:
    """Format number as Indonesian Rupiah."""
    try:
        num = float(amount)
        if num == int(num):
            return f"Rp {int(num):,}".replace(",", ".")
        return f"Rp {num:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    except (ValueError, TypeError):
        return f"Rp {amount}"


class InsightNarrator:
    """
    Convert raw API data to natural language Indonesian response.
    Uses templates for count/sum, LLM for complex lists.
    """

    ENTITY_LABELS = {
        "vendor": "vendor",
        "customer": "pelanggan",
        "product": "produk",
        "bill": "tagihan pembelian",
        "piutang": "piutang",
        "hutang": "hutang",
        "invoice": "faktur penjualan",
        "payment_received": "pembayaran diterima",
        "payment_made": "pembayaran keluar",
        "expense": "pengeluaran",
    }

    async def narrate(
        self,
        template_id: str,
        template: dict,
        result: dict,
        user_text: str,
        planner=None,
    ) -> str:
        """Generate natural language response from query result."""
        result_type = result.get("type", "raw")

        if result_type == "count":
            return self._narrate_count(template, result)

        if result_type == "sum":
            return self._narrate_sum(template, result)

        if result_type == "list":
            return await self._narrate_list(template, result, user_text, planner)

        if result_type == "dashboard":
            return self._narrate_dashboard(template, result)

        return "Data ditemukan tapi saya belum bisa memformatnya."

    def _narrate_dashboard(self, template: dict, result: dict) -> str:
        """Dashboard summary -> formatted text. No LLM needed."""
        data = result.get("data", {})
        entity_type = result.get("entity_type", "")

        if entity_type == "piutang":
            total = data.get("total", 0)
            customer_count = data.get("customer_count", 0)
            jatuh_tempo = data.get("jatuh_tempo", 0)
            current = data.get("current", 0)
            overdue_30 = data.get("overdue_1_30", 0)
            overdue_60 = data.get("overdue_31_60", 0)
            overdue_90 = data.get("overdue_61_90", 0)
            overdue_90_plus = data.get("overdue_90_plus", 0)

            lines = [f"**Ringkasan Piutang:**"]
            lines.append(f"- Total piutang: {_format_rupiah(total)}")
            lines.append(f"- Jumlah pelanggan: {customer_count}")
            if current:
                lines.append(f"- Belum jatuh tempo: {_format_rupiah(current)}")
            if jatuh_tempo:
                lines.append(f"- Jatuh tempo: {_format_rupiah(jatuh_tempo)}")
            if overdue_30:
                lines.append(f"- Overdue 1-30 hari: {_format_rupiah(overdue_30)}")
            if overdue_60:
                lines.append(f"- Overdue 31-60 hari: {_format_rupiah(overdue_60)}")
            if overdue_90:
                lines.append(f"- Overdue 61-90 hari: {_format_rupiah(overdue_90)}")
            if overdue_90_plus:
                lines.append(f"- Overdue >90 hari: {_format_rupiah(overdue_90_plus)}")
            return "\n".join(lines)

        elif entity_type == "hutang":
            total = data.get("total", 0)
            supplier_count = data.get("supplier_count", 0)
            jatuh_tempo = data.get("jatuh_tempo", 0)
            current = data.get("current", 0)

            lines = [f"**Ringkasan Hutang:**"]
            lines.append(f"- Total hutang: {_format_rupiah(total)}")
            lines.append(f"- Jumlah supplier: {supplier_count}")
            if current:
                lines.append(f"- Belum jatuh tempo: {_format_rupiah(current)}")
            if jatuh_tempo:
                lines.append(f"- Jatuh tempo: {_format_rupiah(jatuh_tempo)}")
            overdue_30 = data.get("overdue_1_30", 0)
            overdue_60 = data.get("overdue_31_60", 0)
            overdue_90 = data.get("overdue_61_90", 0)
            overdue_90_plus = data.get("overdue_90_plus", 0)
            if overdue_30:
                lines.append(f"- Overdue 1-30 hari: {_format_rupiah(overdue_30)}")
            if overdue_60:
                lines.append(f"- Overdue 31-60 hari: {_format_rupiah(overdue_60)}")
            if overdue_90:
                lines.append(f"- Overdue 61-90 hari: {_format_rupiah(overdue_90)}")
            if overdue_90_plus:
                lines.append(f"- Overdue >90 hari: {_format_rupiah(overdue_90_plus)}")
            return "\n".join(lines)

        elif entity_type == "expense":
            # top-expenses dashboard
            expenses = data.get("expenses", [])
            total = data.get("total", 0)
            lines = [f"**Pengeluaran Terbesar** (Total: {_format_rupiah(total)}):"]
            for i, exp in enumerate(expenses[:10], 1):
                name = exp.get("category") or exp.get("account_name") or exp.get("name", "\u2014")
                amount = exp.get("amount", 0)
                lines.append(f"{i}. {name}: {_format_rupiah(amount)}")
            if not expenses:
                lines.append("Belum ada data pengeluaran.")
            return "\n".join(lines)

        # Generic dashboard fallback
        import json
        return f"Data dashboard:\n```\n{json.dumps(data, indent=2, default=str, ensure_ascii=False)[:1500]}\n```"

    def _narrate_count(self, template: dict, result: dict) -> str:
        """Count -> simple template string. No LLM needed."""
        hint = template.get("narrator_hint", "Data")
        count = result.get("count", 0)
        entity = self.ENTITY_LABELS.get(result.get("entity_type", ""), "data")
        return f"{hint}: **{count}** {entity} terdaftar."

    def _narrate_sum(self, template: dict, result: dict) -> str:
        """Sum -> formatted number. No LLM needed."""
        hint = template.get("narrator_hint", "Total")
        total = result.get("total", 0)
        count = result.get("count", 0)
        formatted = _format_rupiah(total)
        return f"{hint}: **{formatted}** dari {count} transaksi."

    async def _narrate_list(
        self, template: dict, result: dict, user_text: str, planner=None
    ) -> str:
        """List -> format as numbered list. Use LLM for longer lists."""
        items = result.get("items", [])
        count = result.get("count", 0)
        entity_type = result.get("entity_type", "")
        entity_label = self.ENTITY_LABELS.get(entity_type, "data")
        hint = template.get("narrator_hint", "Daftar")

        if not items:
            return f"Tidak ada {entity_label} yang ditemukan."

        # For short lists (<=10), format directly without LLM
        if len(items) <= 10:
            return self._format_list(hint, items, count, entity_type, entity_label)

        # For longer lists, use LLM if available
        if planner:
            return await self._narrate_list_with_llm(
                hint, items[:15], count, entity_label, user_text, planner
            )

        # Fallback: just show first 10
        return self._format_list(hint, items[:10], count, entity_type, entity_label)

    def _format_list(
        self, hint: str, items: list, total: int, entity_type: str, entity_label: str
    ) -> str:
        """Format a short list of items."""
        lines = [f"{hint} ({total} {entity_label}):"]
        lines.append("")

        for i, item in enumerate(items, 1):
            line = self._format_item(item, entity_type, i)
            lines.append(line)

        if total > len(items):
            remaining = total - len(items)
            lines.append(f"\n...dan {remaining} {entity_label} lainnya.")

        return "\n".join(lines)

    def _format_item(self, item: dict, entity_type: str, index: int) -> str:
        """Format a single item based on entity type."""
        if entity_type == "vendor":
            name = item.get("name") or item.get("display_name", "\u2014")
            phone = item.get("phone", "")
            email = item.get("email", "")
            parts = [f"{index}. **{name}**"]
            if phone:
                parts.append(f"({phone})")
            if email:
                parts.append(f"\u2014 {email}")
            return " ".join(parts)

        elif entity_type == "customer":
            name = item.get("name") or item.get("display_name", "\u2014")
            phone = item.get("phone", "")
            outstanding = item.get("outstanding_balance", 0)
            parts = [f"{index}. **{name}**"]
            if phone:
                parts.append(f"({phone})")
            if outstanding and float(outstanding) > 0:
                parts.append(f"\u2014 Piutang: {_format_rupiah(outstanding)}")
            return " ".join(parts)

        elif entity_type == "product":
            name = item.get("name", "\u2014")
            item_type = item.get("item_type", "")
            price = item.get("sales_price")
            stock = item.get("current_stock", 0)
            parts = [f"{index}. **{name}**"]
            if item_type:
                type_label = "Jasa" if item_type == "service" else "Barang"
                parts.append(f"[{type_label}]")
            if price:
                parts.append(f"\u2014 Harga jual: {_format_rupiah(price)}")
            if stock is not None and float(stock) > 0:
                parts.append(f", Stok: {int(float(stock))}")
            return " ".join(parts)

        elif entity_type == "bill":
            inv_num = item.get("invoice_number", "\u2014")
            vendor = item.get("vendor", {})
            vendor_name = vendor.get("name", "\u2014") if isinstance(vendor, dict) else str(vendor)
            amount = item.get("amount", 0)
            status = item.get("status", "")
            due_date = item.get("due_date", "")
            parts = [f"{index}. **{inv_num}** \u2014 {vendor_name}"]
            parts.append(f"\u2014 {_format_rupiah(amount)}")
            if status:
                status_label = {"unpaid": "Belum dibayar", "paid": "Lunas", "partial": "Sebagian", "void": "Batal"}.get(status, status)
                parts.append(f"({status_label})")
            if due_date:
                parts.append(f"\u2014 Jatuh tempo: {due_date}")
            return " ".join(parts)

        elif entity_type == "invoice":
            inv_num = item.get("invoice_number", "\u2014")
            customer = item.get("customer_name", "\u2014")
            amount = item.get("total_amount", 0)
            paid = item.get("amount_paid", 0)
            status = item.get("status", "")
            status_map = {"draft": "Draft", "posted": "Terkirim", "paid": "Lunas", "void": "Batal", "partial": "Sebagian"}
            status_label = status_map.get(status, status)
            due = item.get("due_date", "")
            parts = [f"{index}. **{inv_num}** \u2014 {customer}"]
            parts.append(f"\u2014 {_format_rupiah(amount)}")
            if status_label:
                parts.append(f"({status_label})")
            if due:
                parts.append(f"\u2014 Jatuh tempo: {due}")
            return " ".join(parts)

        elif entity_type == "payment_received":
            pay_num = item.get("payment_number", "\u2014")
            customer = item.get("customer_name", "\u2014")
            amount = item.get("total_amount", 0)
            method = item.get("payment_method", "")
            date = item.get("payment_date", "")
            parts = [f"{index}. **{pay_num}** \u2014 {customer}"]
            parts.append(f"\u2014 {_format_rupiah(amount)}")
            if method:
                parts.append(f"via {method}")
            if date:
                parts.append(f"({date})")
            return " ".join(parts)

        elif entity_type == "payment_made":
            pay_num = item.get("payment_number", "\u2014")
            vendor = item.get("vendor_name", "\u2014")
            amount = item.get("total_amount", 0)
            method = item.get("payment_method", "")
            date = item.get("payment_date", "")
            parts = [f"{index}. **{pay_num}** \u2014 {vendor}"]
            parts.append(f"\u2014 {_format_rupiah(amount)}")
            if method:
                parts.append(f"via {method}")
            if date:
                parts.append(f"({date})")
            return " ".join(parts)

        elif entity_type == "expense":
            exp_num = item.get("expense_number", "\u2014")
            account = item.get("account_name", "")
            amount = item.get("total_amount", 0)
            status = item.get("status", "")
            date = item.get("expense_date", "")
            vendor = item.get("vendor", {})
            vendor_name = vendor.get("name", "") if isinstance(vendor, dict) else str(vendor) if vendor else ""
            parts = [f"{index}. **{exp_num}**"]
            if account:
                parts.append(f"\u2014 {account}")
            if vendor_name:
                parts.append(f"({vendor_name})")
            parts.append(f"\u2014 {_format_rupiah(amount)}")
            if date:
                parts.append(f"({date})")
            return " ".join(parts)

        elif entity_type == "piutang":
            # For overdue-invoices list
            inv_num = item.get("invoice_number", "\u2014")
            customer = item.get("customer_name", "\u2014")
            amount = item.get("total_amount") or item.get("amount", 0)
            outstanding = item.get("outstanding_amount") or item.get("amount_due", 0)
            due_date = item.get("due_date", "")
            parts = [f"{index}. **{inv_num}** \u2014 {customer}"]
            if outstanding:
                parts.append(f"\u2014 Sisa: {_format_rupiah(outstanding)}")
            elif amount:
                parts.append(f"\u2014 {_format_rupiah(amount)}")
            if due_date:
                parts.append(f"(Jatuh tempo: {due_date})")
            return " ".join(parts)

        else:
            name = item.get("name") or item.get("display_name", str(item.get("id", "\u2014"))[:8])
            return f"{index}. {name}"

    async def _narrate_list_with_llm(
        self, hint: str, items: list, total: int, entity_label: str, user_text: str, planner
    ) -> str:
        """Use LLM to narrate a longer list naturally."""
        import json

        # Simplify items for LLM context
        simplified = []
        for item in items:
            s = {k: v for k, v in item.items() if v is not None and k not in ("id", "created_at", "updated_at")}
            simplified.append(s)

        context = (
            f"User bertanya: \"{user_text}\"\n"
            f"Data ({total} total, menampilkan {len(items)}):\n"
            f"{json.dumps(simplified, indent=2, default=str, ensure_ascii=False)[:3000]}\n\n"
            f"Format data ini dalam Bahasa Indonesia yang natural dan ringkas. "
            f"Gunakan format angka Indonesia (Rp 1.234.567). "
            f"Tampilkan dalam list bernomor. "
            f"Jika total > yang ditampilkan, sebutkan ada berapa lagi. "
            f"JANGAN fabrikasi data \u2014 hanya gunakan data yang diberikan."
        )

        try:
            response = await planner.generate_response(user_text, context=context)
            return response or self._format_list(hint, items, total, "", entity_label)
        except Exception as e:
            logger.warning(f"LLM narration failed, using fallback: {e}")
            return self._format_list(hint, items, total, "", entity_label)
