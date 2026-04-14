#!/usr/bin/env python3
"""
Discovery 100: 50 single-turn + 50 multi-turn chat queries.
Tests routing, data accuracy, latency, and hallucination detection.
"""
import httpx
import json
import time
import asyncio
from datetime import datetime

BASE = "http://localhost:8001"
EMAIL = "grapmanado@gmail.com"
PASSWORD = "grapgrap007"  # pragma: allowlist secret
TENANT = "grapgrap"
TIMEOUT = 25.0
SEP = "=" * 70

SINGLE_QUERIES = [
    {
        "id": "S01",
        "text": "daftar semua barang",
        "expect_intent": "query_items_*",
        "expect_data": True,
    },
    {
        "id": "S02",
        "text": "barang yang stoknya habis",
        "expect_intent": "query_items_no_stock",
        "expect_data": True,
    },
    {
        "id": "S03",
        "text": "stok menipis",
        "expect_intent": "query_items_low_stock",
        "expect_data": True,
    },
    {
        "id": "S04",
        "text": "detail barang Poloshirt Hitam",
        "expect_intent": "query_item_detail",
        "expect_data": True,
    },
    {
        "id": "S05",
        "text": "barang tidak aktif",
        "expect_intent": "query_items_inactive",
        "expect_data": False,
    },
    {
        "id": "S06",
        "text": "daftar kategori barang",
        "expect_intent": "query_categories_list",
        "expect_data": True,
    },
    {
        "id": "S07",
        "text": "daftar gudang",
        "expect_intent": "query_warehouses",
        "expect_data": True,
    },
    {
        "id": "S08",
        "text": "stok per gudang Poloshirt Hitam",
        "expect_intent": "query_warehouse_stock",
        "expect_data": True,
    },
    {
        "id": "S09",
        "text": "barang terlaris",
        "expect_intent": "query_items_top_products",
        "expect_data": False,
    },
    {
        "id": "S10",
        "text": "barang slow moving",
        "expect_intent": "query_items_slow_moving",
        "expect_data": False,
    },
    {
        "id": "S11",
        "text": "daftar pelanggan",
        "expect_intent": "query_customers_list",
        "expect_data": True,
    },
    {
        "id": "S12",
        "text": "detail pelanggan Sintia",
        "expect_intent": "query_customer_detail",
        "expect_data": True,
    },
    {
        "id": "S13",
        "text": "pelanggan dengan piutang tertunggak",
        "expect_intent": "query_customers_with_overdue",
        "expect_data": False,
    },
    {
        "id": "S14",
        "text": "piutang pelanggan Sintia",
        "expect_intent": "query_customer_ar",
        "expect_data": False,
    },
    {
        "id": "S15",
        "text": "berapa jumlah pelanggan aktif",
        "expect_intent": "calc_count_*",
        "expect_data": True,
    },
    {
        "id": "S16",
        "text": "daftar vendor",
        "expect_intent": "query_vendors_list",
        "expect_data": True,
    },
    {
        "id": "S17",
        "text": "detail vendor PT",
        "expect_intent": "query_vendor_detail",
        "expect_data": True,
    },
    {
        "id": "S18",
        "text": "vendor dengan hutang tertunggak",
        "expect_intent": "query_vendors_with_overdue",
        "expect_data": False,
    },
    {
        "id": "S19",
        "text": "hutang ke vendor PT",
        "expect_intent": "query_vendor_ap",
        "expect_data": False,
    },
    {
        "id": "S20",
        "text": "berapa jumlah vendor aktif",
        "expect_intent": "calc_count_*",
        "expect_data": True,
    },
    {
        "id": "S21",
        "text": "total piutang kita berapa",
        "expect_intent": "query_ar_outstanding",
        "expect_data": True,
    },
    {
        "id": "S22",
        "text": "total hutang kita berapa",
        "expect_intent": "query_ap_outstanding",
        "expect_data": True,
    },
    {
        "id": "S23",
        "text": "aging piutang",
        "expect_intent": "query_ar_aging",
        "expect_data": False,
    },
    {
        "id": "S24",
        "text": "aging hutang",
        "expect_intent": "query_ap_aging",
        "expect_data": False,
    },
    {
        "id": "S25",
        "text": "faktur yang sudah jatuh tempo",
        "expect_intent": "query_overdue_all",
        "expect_data": False,
    },
    {
        "id": "S26",
        "text": "daftar rekening bank",
        "expect_intent": "query_bank_accounts_list",
        "expect_data": True,
    },
    {
        "id": "S27",
        "text": "saldo kas dan bank",
        "expect_intent": "query_cash_balance",
        "expect_data": True,
    },
    {
        "id": "S28",
        "text": "total saldo semua rekening",
        "expect_intent": "calc_sum_all_bank*",
        "expect_data": True,
    },
    {
        "id": "S29",
        "text": "transaksi bank bulan ini",
        "expect_intent": "query_bank_transactions*",
        "expect_data": False,
    },
    {
        "id": "S30",
        "text": "daftar transfer bank",
        "expect_intent": "query_bank_transfers*",
        "expect_data": False,
    },
    {
        "id": "S31",
        "text": "daftar faktur penjualan",
        "expect_intent": "query_sales_invoices_list",
        "expect_data": True,
    },
    {
        "id": "S32",
        "text": "ringkasan penjualan bulan ini",
        "expect_intent": "query_sales_invoices_summary",
        "expect_data": True,
    },
    {
        "id": "S33",
        "text": "faktur penjualan yang belum lunas",
        "expect_intent": "query_sales_invoices_unpaid",
        "expect_data": False,
    },
    {
        "id": "S34",
        "text": "faktur penjualan jatuh tempo",
        "expect_intent": "query_sales_invoices_overdue",
        "expect_data": False,
    },
    {
        "id": "S35",
        "text": "total penjualan bulan ini",
        "expect_intent": "calc_sum_sales*",
        "expect_data": True,
    },
    {
        "id": "S36",
        "text": "daftar faktur pembelian",
        "expect_intent": "query_bills_list",
        "expect_data": True,
    },
    {
        "id": "S37",
        "text": "ringkasan pembelian",
        "expect_intent": "query_bills_summary",
        "expect_data": True,
    },
    {
        "id": "S38",
        "text": "tagihan yang belum dibayar",
        "expect_intent": "query_bills_unpaid",
        "expect_data": False,
    },
    {
        "id": "S39",
        "text": "tagihan jatuh tempo",
        "expect_intent": "query_bills_overdue",
        "expect_data": False,
    },
    {
        "id": "S40",
        "text": "total pembelian bulan ini",
        "expect_intent": "calc_sum_purchases*",
        "expect_data": True,
    },
    {
        "id": "S41",
        "text": "daftar pengeluaran",
        "expect_intent": "query_expenses_list",
        "expect_data": True,
    },
    {
        "id": "S42",
        "text": "ringkasan pengeluaran bulan ini",
        "expect_intent": "query_expenses_summary",
        "expect_data": True,
    },
    {
        "id": "S43",
        "text": "pengeluaran terbesar bulan ini",
        "expect_intent": "query_top_expenses",
        "expect_data": False,
    },
    {
        "id": "S44",
        "text": "ringkasan dashboard",
        "expect_intent": "query_dashboard_summary",
        "expect_data": True,
    },
    {
        "id": "S45",
        "text": "neraca saldo",
        "expect_intent": "query_trial_balance",
        "expect_data": False,
    },
    {
        "id": "S46",
        "text": "daftar akun",
        "expect_intent": "query_accounts_list",
        "expect_data": True,
    },
    {
        "id": "S47",
        "text": "daftar jurnal",
        "expect_intent": "query_journals_list",
        "expect_data": False,
    },
    {
        "id": "S48",
        "text": "rata-rata harga jual barang",
        "expect_intent": "calc_avg_harga_jual",
        "expect_data": True,
    },
    {
        "id": "S49",
        "text": "total stok semua barang",
        "expect_intent": "calc_sum_stok",
        "expect_data": True,
    },
    {
        "id": "S50",
        "text": "ranking barang paling mahal",
        "expect_intent": "calc_rank_items*",
        "expect_data": True,
    },
]

MULTI_TURN_GROUPS = [
    {
        "group": "M01",
        "turns": [
            {
                "id": "M01a",
                "text": "siapa saja yang punya piutang",
                "expect_intent": "query_ar*",
            },
            {
                "id": "M01b",
                "text": "totalnya berapa",
                "expect_intent": "query_ar*|calc_*",
            },
            {
                "id": "M01c",
                "text": "yang paling besar siapa",
                "expect_intent": "query_ar*|calc_rank*",
            },
        ],
    },
    {
        "group": "M02",
        "turns": [
            {
                "id": "M02a",
                "text": "hutang kita berapa",
                "expect_intent": "query_ap_outstanding",
            },
            {
                "id": "M02b",
                "text": "rinciannya dong",
                "expect_intent": "contextual_drill*|query_bills*",
            },
            {
                "id": "M02c",
                "text": "tampilkan dalam tabel",
                "expect_intent": "reformat_as_table",
            },
        ],
    },
    {
        "group": "M03",
        "turns": [
            {
                "id": "M03a",
                "text": "ada berapa barang aktif",
                "expect_intent": "calc_count_items*",
            },
            {
                "id": "M03b",
                "text": "yang paling mahal apa",
                "expect_intent": "calc_rank_items*",
            },
            {
                "id": "M03c",
                "text": "stoknya berapa",
                "expect_intent": "query_item*|query_warehouse*",
            },
        ],
    },
    {
        "group": "M04",
        "turns": [
            {
                "id": "M04a",
                "text": "data pelanggan Sintia",
                "expect_intent": "query_customer_detail",
            },
            {
                "id": "M04b",
                "text": "piutangnya berapa",
                "expect_intent": "query_customer_ar",
            },
        ],
    },
    {
        "group": "M05",
        "turns": [
            {
                "id": "M05a",
                "text": "data vendor PT",
                "expect_intent": "query_vendor_detail",
            },
            {
                "id": "M05b",
                "text": "hutangnya berapa",
                "expect_intent": "query_vendor_ap",
            },
        ],
    },
    {
        "group": "M06",
        "turns": [
            {
                "id": "M06a",
                "text": "daftar rekening bank",
                "expect_intent": "query_bank_accounts_list",
            },
            {
                "id": "M06b",
                "text": "saldo masing-masing berapa",
                "expect_intent": "query_cash_balance|query_bank*",
            },
            {
                "id": "M06c",
                "text": "total semuanya",
                "expect_intent": "calc_sum_all_bank*",
            },
        ],
    },
    {
        "group": "M07",
        "turns": [
            {
                "id": "M07a",
                "text": "daftar faktur penjualan",
                "expect_intent": "query_sales_invoices_list",
            },
            {
                "id": "M07b",
                "text": "yang belum lunas mana saja",
                "expect_intent": "query_sales_invoices_unpaid|query_ar*",
            },
        ],
    },
    {
        "group": "M08",
        "turns": [
            {
                "id": "M08a",
                "text": "ringkasan pengeluaran",
                "expect_intent": "query_expenses_summary",
            },
            {
                "id": "M08b",
                "text": "yang terbesar apa",
                "expect_intent": "query_top_expenses|calc_rank*",
            },
        ],
    },
    {
        "group": "M09",
        "turns": [
            {
                "id": "M09a",
                "text": "total piutang",
                "expect_intent": "query_ar_outstanding",
            },
            {
                "id": "M09b",
                "text": "total hutang",
                "expect_intent": "query_ap_outstanding",
            },
            {"id": "M09c", "text": "saldo kas", "expect_intent": "query_cash_balance"},
        ],
    },
    {
        "group": "M10",
        "turns": [
            {
                "id": "M10a",
                "text": "daftar barang beserta stoknya",
                "expect_intent": "query_items_*",
            },
            {
                "id": "M10b",
                "text": "tampilkan dalam tabel yang rapi",
                "expect_intent": "reformat_as_table",
            },
        ],
    },
    {
        "group": "M11",
        "turns": [
            {
                "id": "M11a",
                "text": "daftar tagihan pembelian",
                "expect_intent": "query_bills_list",
            },
            {
                "id": "M11b",
                "text": "per vendor mana yang paling besar",
                "expect_intent": "contextual_drill*|query_bills_by_vendor",
            },
        ],
    },
    {
        "group": "M12",
        "turns": [
            {
                "id": "M12a",
                "text": "cek stok Kain Taslan",
                "expect_intent": "query_item_detail|query_warehouse_stock",
            },
            {
                "id": "M12b",
                "text": "di gudang mana saja",
                "expect_intent": "query_warehouse_stock",
            },
        ],
    },
    {
        "group": "M13",
        "turns": [
            {
                "id": "M13a",
                "text": "berapa rata-rata harga jual",
                "expect_intent": "calc_avg_harga_jual",
            },
            {
                "id": "M13b",
                "text": "kalau harga beli",
                "expect_intent": "calc_avg_harga_beli",
            },
            {
                "id": "M13c",
                "text": "total stok semua barang",
                "expect_intent": "calc_sum_stok",
            },
        ],
    },
    {
        "group": "M14",
        "turns": [
            {
                "id": "M14a",
                "text": "ringkasan keuangan hari ini",
                "expect_intent": "query_dashboard_summary",
            },
            {
                "id": "M14b",
                "text": "faktur yang jatuh tempo",
                "expect_intent": "query_overdue_all|query_*_overdue",
            },
        ],
    },
    {
        "group": "M15",
        "turns": [
            {
                "id": "M15a",
                "text": "minta rekapan dalam tabel stok yang menipis atau habis",
                "expect_intent": "query_items_no_stock|query_items_low_stock",
            },
            {
                "id": "M15b",
                "text": "yang stoknya nol apa saja",
                "expect_intent": "query_items_no_stock",
            },
        ],
    },
    {
        "group": "M16",
        "turns": [
            {
                "id": "M16a",
                "text": "siapa saja pelanggan kita",
                "expect_intent": "query_customers_list",
            },
            {
                "id": "M16b",
                "text": "ada berapa total",
                "expect_intent": "calc_count_customers*",
            },
        ],
    },
    {
        "group": "M17",
        "turns": [
            {
                "id": "M17a",
                "text": "daftar semua vendor",
                "expect_intent": "query_vendors_list",
            },
            {
                "id": "M17b",
                "text": "berapa yang aktif",
                "expect_intent": "calc_count_vendors*",
            },
        ],
    },
    {
        "group": "M18",
        "turns": [
            {
                "id": "M18a",
                "text": "ringkasan penjualan bulan ini",
                "expect_intent": "query_sales_invoices_summary",
            },
            {
                "id": "M18b",
                "text": "berapa yang sudah dibayar",
                "expect_intent": "query_receive_payments*|calc_sum_received*",
            },
        ],
    },
    {
        "group": "M19",
        "turns": [
            {
                "id": "M19a",
                "text": "ringkasan pembelian bulan ini",
                "expect_intent": "query_bills_summary",
            },
            {
                "id": "M19b",
                "text": "berapa yang sudah kita bayar",
                "expect_intent": "query_bill_payments*|calc_sum_paid*",
            },
        ],
    },
    {
        "group": "M20",
        "turns": [
            {
                "id": "M20a",
                "text": "saldo kas dan bank sekarang",
                "expect_intent": "query_cash_balance",
            },
            {
                "id": "M20b",
                "text": "piutang yang belum masuk",
                "expect_intent": "query_ar_outstanding",
            },
            {
                "id": "M20c",
                "text": "hutang yang harus dibayar",
                "expect_intent": "query_ap_outstanding",
            },
        ],
    },
]

HALLUCINATION_KEYWORDS = [
    "kopi arabika",
    "teh melati",
    "gula pasir",
    "susu uht",
    "cokelat bubuk",
    "toko abc",
    "toko xyz",
    "pt abcd",
    "lorem",
    "ipsum",
    "contoh data",
    "sample data",
    "dummy",
]


def check_hallucination(text):
    t = text.lower()
    return [kw for kw in HALLUCINATION_KEYWORDS if kw in t]


def intent_matches(actual, expected):
    for alt in expected.split("|"):
        alt = alt.strip()
        if "*" in alt:
            prefix = alt.replace("*", "")
            if actual.startswith(prefix):
                return True
        elif actual == alt:
            return True
    return False


async def login(client):
    r = await client.post(
        f"{BASE}/api/auth/login",
        json={"email": EMAIL, "password": PASSWORD, "tenant_slug": TENANT},
    )
    return r.json()["data"]["access_token"]


async def send_message(client, token, text, conversation_id):
    start = time.time()
    try:
        r = await client.post(
            f"{BASE}/api/v3/chat/message",
            headers={"Authorization": f"Bearer {token}"},
            json={"text": text, "conversation_id": conversation_id},
            timeout=TIMEOUT,
        )
        latency = int((time.time() - start) * 1000)
        if r.status_code != 200:
            return {
                "error": f"HTTP {r.status_code}: {r.text[:100]}",
                "latency_ms": latency,
            }
        data = r.json()
        tool_calls = data.get("tool_calls") or []
        return {
            "text": data.get("text", "")[:500],
            "message_type": data.get("message_type"),
            "model_used": data.get("model_used"),
            "latency_ms": data.get("latency_ms", latency),
            "iterations": data.get("iterations"),
            "tool_calls": tool_calls,
            "intent": next(
                (
                    tc["args"].get("intent", "")
                    for tc in tool_calls
                    if tc.get("name") == "entity_extractor"
                ),
                "",
            ),
            "endpoint": next(
                (
                    tc["args"].get("endpoint", "")
                    for tc in tool_calls
                    if tc.get("name") == "query_endpoint"
                ),
                "",
            ),
        }
    except Exception as e:
        latency = int((time.time() - start) * 1000)
        return {"error": str(e)[:200], "latency_ms": latency}


def analyze(q, r):
    issues = []
    if "error" in r:
        issues.append(f"ERROR: {r['error'][:80]}")
        return issues

    actual_intent = r.get("intent", "")
    if actual_intent and not intent_matches(actual_intent, q["expect_intent"]):
        issues.append(f"INTENT: got={actual_intent} expected={q['expect_intent']}")

    halluc = check_hallucination(r.get("text", ""))
    if halluc:
        issues.append(f"HALLUCINATION: {halluc}")

    lat = r.get("latency_ms", 0)
    if lat > 10000:
        issues.append(f"SLOW: {lat}ms (>10s)")

    if r.get("text", "").strip() == "":
        issues.append("EMPTY_RESPONSE")

    txt = r.get("text", "").lower()
    if q.get("expect_data") and (
        "tidak ada" in txt or "tidak ditemukan" in txt or "belum ada" in txt
    ):
        no_data_phrases = [
            "tidak ada item",
            "tidak ada barang",
            "tidak ada pelanggan",
            "tidak ada vendor",
            "tidak ada rekening",
            "tidak ada akun",
            "tidak ada data",
            "belum ada data",
            "saat ini tidak ada",
        ]
        if any(p in txt for p in no_data_phrases):
            issues.append("FALSE_EMPTY: expected data but got 'tidak ada'")

    return issues


async def run_single(client, token, results):
    print(f"\n{SEP}")
    print("SINGLE-TURN QUERIES (50)")
    print(f"{SEP}\n")

    for q in SINGLE_QUERIES:
        conv_id = f"disc-s-{q['id']}-{int(time.time())}"
        r = await send_message(client, token, q["text"], conv_id)
        issues = analyze(q, r)

        status = "FAIL" if issues else "PASS"
        lat_str = f"{r.get('latency_ms', 0):>5}ms"
        intent_str = r.get("intent", "?")[:30]
        model_str = (r.get("model_used", "?") or "?")[:20]

        result = {
            "id": q["id"],
            "text": q["text"],
            "status": status,
            "latency_ms": r.get("latency_ms", 0),
            "intent": intent_str,
            "model": model_str,
            "endpoint": r.get("endpoint", ""),
            "issues": issues,
            "response_preview": r.get("text", "")[:200],
        }
        results.append(result)

        icon = "PASS" if status == "PASS" else "FAIL"
        print(f"  {icon} {q['id']} [{lat_str}] [{intent_str:<30}] {q['text'][:50]}")
        if issues:
            for iss in issues:
                print(f"       >> {iss}")

        await asyncio.sleep(0.3)


async def run_multi(client, token, results):
    print(f"\n{SEP}")
    print(
        f"MULTI-TURN QUERIES ({sum(len(g['turns']) for g in MULTI_TURN_GROUPS)} turns, {len(MULTI_TURN_GROUPS)} groups)"
    )
    print(f"{SEP}\n")

    for group in MULTI_TURN_GROUPS:
        conv_id = f"disc-m-{group['group']}-{int(time.time())}"
        print(f"\n-- Group {group['group']} --")

        for turn in group["turns"]:
            r = await send_message(client, token, turn["text"], conv_id)
            issues = analyze(turn, r)

            status = "FAIL" if issues else "PASS"
            lat_str = f"{r.get('latency_ms', 0):>5}ms"
            intent_str = r.get("intent", "?")[:30]

            result = {
                "id": turn["id"],
                "group": group["group"],
                "text": turn["text"],
                "status": status,
                "latency_ms": r.get("latency_ms", 0),
                "intent": intent_str,
                "model": (r.get("model_used", "?") or "?")[:20],
                "endpoint": r.get("endpoint", ""),
                "issues": issues,
                "response_preview": r.get("text", "")[:200],
            }
            results.append(result)

            icon = "PASS" if status == "PASS" else "FAIL"
            print(
                f"  {icon} {turn['id']} [{lat_str}] [{intent_str:<30}] {turn['text'][:50]}"
            )
            if issues:
                for iss in issues:
                    print(f"       >> {iss}")

            await asyncio.sleep(0.3)


async def main():
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"Discovery 100 -- {ts}")
    print(f"Target: {BASE} | Tenant: {TENANT}\n")

    async with httpx.AsyncClient(timeout=30.0) as client:
        token = await login(client)
        print("Logged in OK\n")

        results = []
        await run_single(client, token, results)
        await run_multi(client, token, results)

        # SUMMARY
        print(f"\n{SEP}")
        print("SUMMARY")
        print(f"{SEP}\n")

        total = len(results)
        passed = sum(1 for r in results if r["status"] == "PASS")
        failed = total - passed

        latencies = [r["latency_ms"] for r in results if r.get("latency_ms")]
        avg_lat = sum(latencies) / len(latencies) if latencies else 0
        max_lat = max(latencies) if latencies else 0
        sorted_lat = sorted(latencies)
        p90_lat = sorted_lat[int(len(sorted_lat) * 0.9)] if sorted_lat else 0

        print(f"Total:   {total}")
        print(f"Passed:  {passed} ({passed * 100 // total}%)")
        print(f"Failed:  {failed} ({failed * 100 // total}%)")
        print(f"Avg lat: {avg_lat:.0f}ms")
        print(f"P90 lat: {p90_lat}ms")
        print(f"Max lat: {max_lat}ms")

        intent_fails = [
            r for r in results if any("INTENT" in i for i in r.get("issues", []))
        ]
        halluc_fails = [
            r for r in results if any("HALLUCINATION" in i for i in r.get("issues", []))
        ]
        error_fails = [
            r for r in results if any("ERROR" in i for i in r.get("issues", []))
        ]
        slow_fails = [
            r for r in results if any("SLOW" in i for i in r.get("issues", []))
        ]
        empty_fails = [
            r
            for r in results
            if any(
                "FALSE_EMPTY" in i or "EMPTY_RESPONSE" in i for i in r.get("issues", [])
            )
        ]

        if intent_fails:
            print(f"\nINTENT MISMATCHES ({len(intent_fails)}):")
            for r in intent_fails:
                iss = [i for i in r["issues"] if "INTENT" in i][0]
                print(f"   {r['id']}: {r['text'][:50]} -> {iss}")

        if halluc_fails:
            print(f"\nHALLUCINATIONS ({len(halluc_fails)}):")
            for r in halluc_fails:
                iss = [i for i in r["issues"] if "HALLUCINATION" in i][0]
                print(f"   {r['id']}: {r['text'][:50]} -> {iss}")

        if error_fails:
            print(f"\nERRORS ({len(error_fails)}):")
            for r in error_fails:
                iss = [i for i in r["issues"] if "ERROR" in i][0]
                print(f"   {r['id']}: {r['text'][:50]} -> {iss}")

        if slow_fails:
            print(f"\nSLOW >10s ({len(slow_fails)}):")
            for r in slow_fails:
                print(f"   {r['id']}: {r['text'][:50]} -> {r['latency_ms']}ms")

        if empty_fails:
            print(f"\nFALSE EMPTY ({len(empty_fails)}):")
            for r in empty_fails:
                print(f"   {r['id']}: {r['text'][:50]}")

        out_path = "/root/milkyhoop-dev/backend/api_gateway/tests/chat/discovery_100_results.json"
        with open(out_path, "w") as f:
            json.dump(
                {
                    "timestamp": datetime.now().isoformat(),
                    "total": total,
                    "passed": passed,
                    "failed": failed,
                    "avg_latency_ms": round(avg_lat),
                    "p90_latency_ms": p90_lat,
                    "max_latency_ms": max_lat,
                    "results": results,
                },
                f,
                indent=2,
                ensure_ascii=False,
            )
        print(f"\nFull results: {out_path}")


if __name__ == "__main__":
    asyncio.run(main())
