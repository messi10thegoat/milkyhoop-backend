#!/usr/bin/env python3
"""LLM Router Prompt Tuning — labeled test queries via shadow comparison."""
import asyncio
import httpx
import json
import time
import uuid
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional

BASE_URL = "http://localhost:8001"
EMAIL = "grapmanado@gmail.com"
PASSWORD = "grapgrap007"
TIMEOUT = 45
DELAY = 0.3

@dataclass
class TC:
    id: str
    text: str
    expected: str
    tags: list = field(default_factory=list)
    group: str = ""
    step: int = 0
    note: str = ""

# ═══ SINGLE-TURN (120) ═══
S = [
    # CRUD CREATE (20)
    TC("c01", "buat vendor baru PT Makmur", "create_vendor", ["crud"]),
    TC("c02", "tambah pelanggan Sintia Runtuwene", "create_customer", ["crud"]),
    TC("c03", "bikin barang baru Poloshirt Hitam", "create_item", ["crud"]),
    TC("c04", "buat faktur penjualan untuk PT Maju", "create_sales_invoice", ["crud"]),
    TC("c05", "catat pembelian dari Knitto 500rb", "create_bill", ["crud"]),
    TC("c06", "catat biaya listrik 450 ribu", "create_expense", ["crud"]),
    TC("c07", "buat jurnal umum", "create_journal_entry", ["crud"]),
    TC("c08", "buat gudang baru Gudang Utama", "create_warehouse", ["crud"]),
    TC("c09", "tambah rekening bank BCA", "create_bank_account", ["crud"]),
    TC("c10", "buat akun baru Beban Marketing", "create_account", ["crud"]),
    TC("c11", "daftarkan vendor PD Suryatex alamat Bandung telepon 022-123", "create_vendor", ["crud", "multi_field"]),
    TC("c12", "buat vendor baru dengan rekening BCA 3790-685-333", "create_vendor", ["crud", "multi_keyword"]),
    TC("c13", "tambah pelanggan baru PT Jaya alamat Jakarta", "create_customer", ["crud", "multi_field"]),
    TC("c14", "buat nota kredit untuk faktur INV-0023", "create_credit_note", ["crud"]),
    TC("c15", "buat penawaran harga untuk PT Maju", "create_quote", ["crud"]),
    TC("c16", "catat transfer dari BCA ke Mandiri 5 juta", "create_bank_transfer", ["crud"]),
    TC("c17", "buat deposit pelanggan Sintia 2 juta", "create_customer_deposit", ["crud"]),
    TC("c18", "buat deposit vendor Knitto 1 juta", "create_vendor_deposit", ["crud"]),
    TC("c19", "buat penyesuaian stok Poloshirt Hitam tambah 50", "create_stock_adjustment", ["crud"]),
    TC("c20", "bikin kredit vendor untuk Knitto", "create_vendor_credit", ["crud"]),
    # UPDATE/DELETE/VOID (10)
    TC("u01", "edit vendor Knitto ubah telepon jadi 081234567", "update_vendor", ["crud"]),
    TC("u02", "ubah harga jual Poloshirt Hitam jadi 175000", "update_item", ["crud"]),
    TC("u03", "ganti alamat pelanggan Sintia ke Bandung", "update_customer", ["crud"]),
    TC("u04", "hapus vendor PT Test", "delete_vendor", ["crud"]),
    TC("u05", "hapus barang Poloshirt Putih", "delete_item", ["crud"]),
    TC("u06", "void faktur penjualan INV-0023", "void_sales_invoice", ["crud"]),
    TC("u07", "batalkan faktur pembelian PB-0001", "void_bill", ["crud"]),
    TC("u08", "void pembayaran PAY-0001", "void_bill_payment", ["crud"]),
    TC("u09", "batalkan expense EXP-0001", "void_expense", ["crud"]),
    TC("u10", "reverse jurnal JN-0001", "reverse_journal", ["crud"]),
    # QUERY AR/AP (15)
    TC("q01", "hutang kita berapa", "query_ap_outstanding", ["query", "arap"]),
    TC("q02", "piutang berapa total", "query_ar_outstanding", ["query", "arap"]),
    TC("q03", "hutang saya berapa", "query_ap_outstanding", ["query", "arap"]),
    TC("q04", "piutang siapa saja", "query_ar_invoices", ["query", "arap"]),
    TC("q05", "piutang Sintia berapa", "query_customer_ar", ["query", "arap"]),
    TC("q06", "hutang ke Knitto berapa", "query_vendor_ap", ["query", "arap"]),
    TC("q07", "siapa yang punya piutang", "query_ar_invoices", ["query", "arap"]),
    TC("q08", "vendor mana yang kita hutangi", "query_ap_outstanding", ["query", "arap"]),
    TC("q09", "faktur yang belum dibayar", "query_sales_invoices_unpaid", ["query", "arap"]),
    TC("q10", "tagihan yang belum lunas", "query_bills_unpaid", ["query", "arap"]),
    TC("q11", "faktur overdue", "query_sales_invoices_overdue", ["query", "arap"]),
    TC("q12", "tagihan jatuh tempo", "query_bills_overdue", ["query", "arap"]),
    TC("q13", "aging piutang", "query_ar_aging", ["query", "arap"]),
    TC("q14", "aging hutang", "query_ap_aging", ["query", "arap"]),
    TC("q15", "pelanggan yang piutangnya overdue", "query_customers_with_overdue", ["query", "arap"]),
    # QUERY items (10)
    TC("qi01", "daftar barang", "query_items_search", ["query", "items"]),
    TC("qi02", "list semua produk", "query_items_search", ["query", "items"]),
    TC("qi03", "stok Poloshirt Hitam", "query_warehouse_stock", ["query", "items"]),
    TC("qi04", "barang yang stoknya habis", "query_items_no_stock", ["query", "items"]),
    TC("qi05", "barang yang stoknya rendah", "query_items_low_stock", ["query", "items"]),
    TC("qi06", "detail barang Keran Wastafel", "query_item_detail", ["query", "items"]),
    TC("qi07", "berapa jenis barang aktif", "calc_count_items_active", ["query", "items", "calc"]),
    TC("qi08", "ringkasan barang", "query_items_summary", ["query", "items"]),
    TC("qi09", "cari barang poloshirt", "query_items_search", ["query", "items"]),
    TC("qi10", "daftar kategori barang", "query_categories_list", ["query", "items"]),
    # QUERY bills/invoices/expenses (10)
    TC("qf01", "daftar faktur penjualan", "query_sales_invoices_list", ["query", "invoice"]),
    TC("qf02", "daftar faktur pembelian", "query_bills_list", ["query", "bill"]),
    TC("qf03", "detail faktur INV-0023", "query_sales_invoice_detail", ["query", "invoice"]),
    TC("qf04", "ringkasan penjualan bulan ini", "query_sales_invoices_summary", ["query", "invoice"]),
    TC("qf05", "ringkasan pembelian", "query_bills_summary", ["query", "bill"]),
    TC("qf06", "daftar pengeluaran", "query_expenses_list", ["query", "expense"]),
    TC("qf07", "ringkasan biaya bulan ini", "query_expenses_summary", ["query", "expense"]),
    TC("qf08", "biaya apa saja bulan ini", "query_expenses_list", ["query", "expense"]),
    TC("qf09", "laba rugi bulan ini", "query_profit_loss", ["query", "report"]),
    TC("qf10", "neraca saldo", "query_trial_balance", ["query", "report"]),
    # QUERY bank/customer/vendor (10)
    TC("qb01", "saldo semua rekening", "query_bank_accounts_list", ["query", "bank"]),
    TC("qb02", "saldo BCA berapa", "query_bank_account_balance", ["query", "bank"]),
    TC("qb03", "daftar pelanggan", "query_customers_list", ["query", "customer"]),
    TC("qb04", "data lengkap Sintia", "query_customer_detail", ["query", "customer"]),
    TC("qb05", "daftar vendor", "query_vendors_list", ["query", "vendor"]),
    TC("qb06", "detail vendor Knitto", "query_vendor_detail", ["query", "vendor"]),
    TC("qb07", "daftar akun", "query_accounts_list", ["query", "coa"]),
    TC("qb08", "mutasi BCA bulan ini", "query_bank_transactions", ["query", "bank"]),
    TC("qb09", "daftar pembayaran masuk", "query_receive_payments_list", ["query", "payment"]),
    TC("qb10", "daftar pembayaran keluar", "query_bill_payments_list", ["query", "payment"]),
    # CALC (15)
    TC("ca01", "rata-rata harga jual", "calc_avg_harga_jual", ["calc"]),
    TC("ca02", "total harga beli semua barang", "calc_sum_harga_beli", ["calc"]),
    TC("ca03", "total stok semua barang", "calc_sum_stok", ["calc"]),
    TC("ca04", "barang termahal", "calc_rank_items_by_price", ["calc"]),
    TC("ca05", "barang stok terbanyak", "calc_rank_items_by_stock", ["calc"]),
    TC("ca06", "berapa pelanggan aktif", "calc_count_customers_active", ["calc"]),
    TC("ca07", "berapa vendor aktif", "calc_count_vendors_active", ["calc"]),
    TC("ca08", "total penjualan bulan ini", "calc_sum_sales_this_month", ["calc"]),
    TC("ca09", "total pembelian bulan ini", "calc_sum_purchases_this_month", ["calc"]),
    TC("ca10", "total pengeluaran bulan ini", "calc_sum_expenses_this_month", ["calc"]),
    TC("ca11", "total pembayaran masuk bulan ini", "calc_sum_received_this_month", ["calc"]),
    TC("ca12", "total pembayaran keluar bulan ini", "calc_sum_paid_this_month", ["calc"]),
    TC("ca13", "total saldo semua rekening", "calc_sum_all_bank_balances", ["calc"]),
    TC("ca14", "pelanggan piutang terbesar", "calc_rank_customers_by_ar", ["calc"]),
    TC("ca15", "vendor hutang terbesar", "calc_rank_vendors_by_ap", ["calc"]),
    # PREFIX NOISE (10)
    TC("pn01", "ok, kalau piutang berapa total?", "query_ar_outstanding", ["prefix_noise"]),
    TC("pn02", "terus hutang kita gimana?", "query_ap_outstanding", ["prefix_noise"]),
    TC("pn03", "nah saldo BCA berapa sih?", "query_bank_account_balance", ["prefix_noise"]),
    TC("pn04", "eh ada barang yang stoknya habis ga?", "query_items_no_stock", ["prefix_noise"]),
    TC("pn05", "btw, buat vendor baru dong namanya PT Test", "create_vendor", ["prefix_noise"]),
    TC("pn06", "oh iya, faktur yang belum lunas ada berapa?", "query_sales_invoices_unpaid", ["prefix_noise"]),
    TC("pn07", "omong-omong, hutang ke Knitto berapa ya?", "query_vendor_ap", ["prefix_noise"]),
    TC("pn08", "hmm, daftar barang dong", "query_items_search", ["prefix_noise"]),
    TC("pn09", "oke oke, buat faktur untuk Sintia", "create_sales_invoice", ["prefix_noise"]),
    TC("pn10", "ya udah, bikin expense listrik 200rb", "create_expense", ["prefix_noise"]),
    # MULTI-KEYWORD (10)
    TC("mk01", "daftarkan vendor baru dengan rekening BCA", "create_vendor", ["multi_keyword"]),
    TC("mk02", "buat pelanggan baru termasuk data rekening", "create_customer", ["multi_keyword"]),
    TC("mk03", "vendor Knitto kirim barang Poloshirt", "create_bill", ["multi_keyword"]),
    TC("mk04", "faktur penjualan untuk vendor... eh pelanggan Sintia", "create_sales_invoice", ["multi_keyword"]),
    TC("mk05", "catat pembayaran hutang ke BCA", "create_bill_payment", ["multi_keyword"]),
    TC("mk06", "terima pembayaran piutang dari Sintia ke BCA", "create_receive_payment", ["multi_keyword"]),
    TC("mk07", "buat faktur pembelian barang dari Knitto", "create_bill", ["multi_keyword"]),
    TC("mk08", "catat biaya transport dari rekening BCA", "create_expense", ["multi_keyword"]),
    TC("mk09", "bayar tagihan Knitto dari Mandiri", "create_bill_payment", ["multi_keyword"]),
    TC("mk10", "terima bayaran faktur INV-0023 ke BCA", "create_receive_payment", ["multi_keyword"]),
    # CHITCHAT (10)
    TC("ch01", "halo", "chitchat", ["chitchat"]),
    TC("ch02", "terima kasih", "chitchat", ["chitchat"]),
    TC("ch03", "oke", "chitchat", ["chitchat"]),
    TC("ch04", "makasih ya", "chitchat", ["chitchat"]),
    TC("ch05", "hai", "chitchat", ["chitchat"]),
    TC("ch06", "selamat pagi", "chitchat", ["chitchat"]),
    TC("ch07", "siap", "chitchat", ["chitchat"]),
    TC("ch08", "baik", "chitchat", ["chitchat"]),
    TC("ch09", "good morning", "chitchat", ["chitchat"]),
    TC("ch10", "mantap", "chitchat", ["chitchat"]),
    # EDGE (5)
    TC("e01", "berapa", "chitchat", ["edge"]),
    TC("e02", "poloshirt", "query_item_detail", ["edge"]),
    TC("e03", "sintia", "query_customer_detail", ["edge"]),
    TC("e04", "100000", "chitchat", ["edge"]),
    TC("e05", "ploshirt hitma", "query_items_search", ["edge"]),
]

# ═══ MULTI-TURN (16 groups) ═══
M = [
    # MT01: Create Invoice (5)
    TC("mt01_1", "buat faktur penjualan", "create_sales_invoice", ["multi_turn"], "mt01", 1),
    TC("mt01_2", "sintia", "create_sales_invoice", ["multi_turn", "slot_fill"], "mt01", 2, "slot fill NOT query"),
    TC("mt01_3", "poloshirt hitam 20 pcs harga 150000", "create_sales_invoice", ["multi_turn", "slot_fill"], "mt01", 3),
    TC("mt01_4", "besok", "create_sales_invoice", ["multi_turn", "slot_fill"], "mt01", 4),
    TC("mt01_5", "oke terbitkan", "create_sales_invoice", ["multi_turn", "slot_fill"], "mt01", 5),
    # MT02: Create Invoice different pattern (5)
    TC("mt02_1", "mau buat faktur", "create_sales_invoice", ["multi_turn"], "mt02", 1),
    TC("mt02_2", "PT Maju Jaya", "create_sales_invoice", ["multi_turn", "slot_fill"], "mt02", 2),
    TC("mt02_3", "keran wastafel 5 pcs", "create_sales_invoice", ["multi_turn", "slot_fill"], "mt02", 3),
    TC("mt02_4", "harga 250000 per pcs", "create_sales_invoice", ["multi_turn", "slot_fill"], "mt02", 4),
    TC("mt02_5", "jatuh tempo minggu depan", "create_sales_invoice", ["multi_turn", "slot_fill"], "mt02", 5),
    # MT03: One-shot invoice (2)
    TC("mt03_1", "buat faktur penjualan untuk Sintia poloshirt 10 pcs 150rb", "create_sales_invoice", ["multi_turn"], "mt03", 1),
    TC("mt03_2", "terbitkan", "create_sales_invoice", ["multi_turn", "slot_fill"], "mt03", 2),
    # MT04: Invoice with correction (3)
    TC("mt04_1", "buat faktur untuk Sintia poloshirt 20 pcs 150rb", "create_sales_invoice", ["multi_turn"], "mt04", 1),
    TC("mt04_2", "eh salah, bukan 20 tapi 30 pcs", "create_sales_invoice", ["multi_turn", "correction"], "mt04", 2),
    TC("mt04_3", "oke betul", "create_sales_invoice", ["multi_turn", "slot_fill"], "mt04", 3),
    # MT05: Create Vendor (5)
    TC("mt05_1", "buat vendor baru", "create_vendor", ["multi_turn"], "mt05", 1),
    TC("mt05_2", "PD Suryatex", "create_vendor", ["multi_turn", "slot_fill"], "mt05", 2),
    TC("mt05_3", "alamat Bandung, telepon 022-5408799", "create_vendor", ["multi_turn", "slot_fill"], "mt05", 3),
    TC("mt05_4", "rekening BCA 3790-685-333 atas nama Citra", "create_vendor", ["multi_turn", "slot_fill"], "mt05", 4),
    TC("mt05_5", "barang", "create_vendor", ["multi_turn", "slot_fill"], "mt05", 5, "barang/jasa answer NOT query"),
    # MT06: Create Customer (4)
    TC("mt06_1", "tambah pelanggan baru", "create_customer", ["multi_turn"], "mt06", 1),
    TC("mt06_2", "Sintia Runtuwene", "create_customer", ["multi_turn", "slot_fill"], "mt06", 2),
    TC("mt06_3", "Manado, telepon 0431-123456", "create_customer", ["multi_turn", "slot_fill"], "mt06", 3),
    TC("mt06_4", "oke simpan", "create_customer", ["multi_turn", "slot_fill"], "mt06", 4),
    # MT07: Vendor one-shot (2)
    TC("mt07_1", "daftarkan vendor PT Makmur Sentosa alamat Jakarta Selatan telepon 021-7891234", "create_vendor", ["multi_turn"], "mt07", 1),
    TC("mt07_2", "jasa", "create_vendor", ["multi_turn", "slot_fill"], "mt07", 2, "barang/jasa NOT query"),
    # MT08: Create Bill (4)
    TC("mt08_1", "catat pembelian dari Knitto", "create_bill", ["multi_turn"], "mt08", 1),
    TC("mt08_2", "kain hitam 100 meter harga 35000", "create_bill", ["multi_turn", "slot_fill"], "mt08", 2),
    TC("mt08_3", "nomor faktur PB-2604-001", "create_bill", ["multi_turn", "slot_fill"], "mt08", 3),
    TC("mt08_4", "terbitkan", "create_bill", ["multi_turn", "slot_fill"], "mt08", 4),
    # MT09: Query → Follow-up → Action (5)
    TC("mt09_1", "hutang kita berapa", "query_ap_outstanding", ["multi_turn", "query"], "mt09", 1),
    TC("mt09_2", "ke vendor mana saja", "query_ap_outstanding", ["multi_turn", "follow_up"], "mt09", 2),
    TC("mt09_3", "yang paling besar siapa", "calc_rank_vendors_by_ap", ["multi_turn", "follow_up"], "mt09", 3),
    TC("mt09_4", "bayar yang Knitto", "create_bill_payment", ["multi_turn", "action_from_query"], "mt09", 4),
    TC("mt09_5", "dari BCA", "create_bill_payment", ["multi_turn", "slot_fill"], "mt09", 5),
    # MT10: Query AR follow-up (4)
    TC("mt10_1", "piutang berapa total", "query_ar_outstanding", ["multi_turn", "query"], "mt10", 1),
    TC("mt10_2", "dari siapa saja", "query_ar_invoices", ["multi_turn", "follow_up"], "mt10", 2),
    TC("mt10_3", "yang terbesar siapa", "calc_rank_customers_by_ar", ["multi_turn", "follow_up"], "mt10", 3),
    TC("mt10_4", "detail piutang Sintia", "query_customer_ar", ["multi_turn", "follow_up"], "mt10", 4),
    # MT11: Drill-down (3)
    TC("mt11_1", "ringkasan pengeluaran bulan ini", "query_expenses_summary", ["multi_turn", "query"], "mt11", 1),
    TC("mt11_2", "rinciannya dong", "query_expenses_list", ["multi_turn", "drill_down"], "mt11", 2),
    TC("mt11_3", "tampilkan dalam tabel", "reformat_as_table", ["multi_turn", "reformat"], "mt11", 3),
    # MT12: Slot fill stress (5)
    TC("mt12_1", "buat faktur penjualan", "create_sales_invoice", ["multi_turn", "stress"], "mt12", 1),
    TC("mt12_2", "barang", "create_sales_invoice", ["multi_turn", "slot_fill", "stress"], "mt12", 2, "item_type NOT query"),
    TC("mt12_3", "poloshirt", "create_sales_invoice", ["multi_turn", "slot_fill", "stress"], "mt12", 3),
    TC("mt12_4", "20", "create_sales_invoice", ["multi_turn", "slot_fill", "stress"], "mt12", 4),
    TC("mt12_5", "150000", "create_sales_invoice", ["multi_turn", "slot_fill", "stress"], "mt12", 5),
    # MT13: Domain switch (4)
    TC("mt13_1", "piutang berapa", "query_ar_outstanding", ["multi_turn", "domain_switch"], "mt13", 1),
    TC("mt13_2", "hutang berapa", "query_ap_outstanding", ["multi_turn", "domain_switch"], "mt13", 2),
    TC("mt13_3", "daftar barang", "query_items_search", ["multi_turn", "domain_switch"], "mt13", 3),
    TC("mt13_4", "buat vendor baru PT ABC", "create_vendor", ["multi_turn", "domain_switch"], "mt13", 4),
    # MT14: Cancel workflow (3)
    TC("mt14_1", "buat faktur penjualan", "create_sales_invoice", ["multi_turn", "cancel"], "mt14", 1),
    TC("mt14_2", "batal", "chitchat", ["multi_turn", "cancel"], "mt14", 2),
    TC("mt14_3", "hutang berapa", "query_ap_outstanding", ["multi_turn"], "mt14", 3),
    # MT15: Expense workflow (3)
    TC("mt15_1", "catat biaya", "create_expense", ["multi_turn"], "mt15", 1),
    TC("mt15_2", "listrik 450 ribu", "create_expense", ["multi_turn", "slot_fill"], "mt15", 2),
    TC("mt15_3", "dari BCA", "create_expense", ["multi_turn", "slot_fill"], "mt15", 3),
    # MT16: Payment workflow (5)
    TC("mt16_1", "mau bayar tagihan", "create_bill_payment", ["multi_turn"], "mt16", 1),
    TC("mt16_2", "Knitto", "create_bill_payment", ["multi_turn", "slot_fill"], "mt16", 2),
    TC("mt16_3", "500 ribu", "create_bill_payment", ["multi_turn", "slot_fill"], "mt16", 3),
    TC("mt16_4", "BCA", "create_bill_payment", ["multi_turn", "slot_fill"], "mt16", 4),
    TC("mt16_5", "oke bayar", "create_bill_payment", ["multi_turn", "slot_fill"], "mt16", 5),
]

async def run(mode="all"):
    async with httpx.AsyncClient(timeout=TIMEOUT, base_url=BASE_URL) as c:
        r = await c.post("/api/auth/login", json={"email": EMAIL, "password": PASSWORD})
        token = r.json()["data"]["access_token"]
        h = {"Authorization": f"Bearer {token}"}

        results = []

        if mode in ("all", "single"):
            print(f"\n{'='*60}\nSINGLE-TURN ({len(S)} cases)\n{'='*60}")
            for tc in S:
                conv = str(uuid.uuid4())
                t0 = time.time()
                try:
                    r = await c.post("/api/v3/chat/message", json={"conversation_id": conv, "text": tc.text}, headers=h)
                    d = r.json()
                    ms = int((time.time()-t0)*1000)
                    results.append({"id": tc.id, "text": tc.text, "expected": tc.expected,
                                   "tags": tc.tags, "mt": d.get("message_type",""), "ms": ms,
                                   "resp": (d.get("text") or "")[:80]})
                    print(f"  {tc.id}: [{ms:5d}ms] {tc.text[:45]}")
                except Exception as e:
                    results.append({"id": tc.id, "text": tc.text, "expected": tc.expected, "error": str(e)[:100]})
                    print(f"  {tc.id}: ERROR {e}")
                await asyncio.sleep(DELAY)

        if mode in ("all", "multi"):
            groups = defaultdict(list)
            for tc in M:
                groups[tc.group].append(tc)
            for g in groups:
                groups[g].sort(key=lambda x: x.step)

            print(f"\n{'='*60}\nMULTI-TURN ({len(groups)} groups, {len(M)} turns)\n{'='*60}")
            for gid, cases in sorted(groups.items()):
                print(f"\n--- {gid} ---")
                conv = str(uuid.uuid4())
                sid = None
                for tc in cases:
                    t0 = time.time()
                    payload = {"conversation_id": conv, "text": tc.text}
                    if sid:
                        payload["session_id"] = sid
                    try:
                        r = await c.post("/api/v3/chat/message", json=payload, headers=h)
                        d = r.json()
                        ms = int((time.time()-t0)*1000)
                        if d.get("session_id"):
                            sid = d["session_id"]
                        note = f" ({tc.note})" if tc.note else ""
                        results.append({"id": tc.id, "text": tc.text, "expected": tc.expected,
                                       "tags": tc.tags, "group": tc.group, "step": tc.step,
                                       "mt": d.get("message_type",""), "ms": ms,
                                       "resp": (d.get("text") or "")[:80]})
                        print(f"  T{tc.step}: [{ms:5d}ms] {tc.text[:40]}{note}")
                    except Exception as e:
                        results.append({"id": tc.id, "text": tc.text, "expected": tc.expected, "error": str(e)[:100]})
                        print(f"  T{tc.step}: ERROR {e}")
                    await asyncio.sleep(1.0 if tc.group else DELAY)

        # Save
        with open("router_tuning_results.json", "w") as f:
            json.dump(results, f, indent=2, ensure_ascii=False)
        print(f"\nSaved {len(results)} results to router_tuning_results.json")
        print(f"\nNow run: docker logs milkyhoop-dev-api_gateway 2>&1 | grep SHADOW | tail -300")

if __name__ == "__main__":
    mode = "all"
    if "--single" in sys.argv: mode = "single"
    if "--multi" in sys.argv: mode = "multi"
    asyncio.run(run(mode))
