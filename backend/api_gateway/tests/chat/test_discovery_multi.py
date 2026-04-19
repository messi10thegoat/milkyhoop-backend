#!/usr/bin/env python3
"""
Discovery Multi-Turn: 50 turns across 20 conversation groups.
Tests routing, follow-up, pronoun resolution, domain continuity, and response quality.
"""
import httpx
import json
import time
import asyncio
from datetime import datetime

BASE = "http://localhost:8001"
EMAIL = "grapmanado@gmail.com"
PASSWORD = "grapgrap007"
TENANT = "grapgrap"
TIMEOUT = 25.0
SEP = "=" * 70

MULTI_TURN_GROUPS = [
    {
        "group": "M01",
        "desc": "AR flow + pronoun",
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
        "desc": "AP flow + drill down",
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
        "desc": "Items count + rank + follow-up",
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
                "expect_intent": "query_item*|query_warehouse*|calc_*",
            },
        ],
    },
    {
        "group": "M04",
        "desc": "Customer detail + AR",
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
        "desc": "Vendor detail + AP",
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
        "desc": "Bank list + balance + total",
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
        "desc": "Invoice list + unpaid filter",
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
        "desc": "Expense summary + top",
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
        "desc": "Domain switch: AR -> AP -> Bank",
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
        "desc": "Items list + reformat",
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
        "desc": "Bills + vendor drill",
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
        "desc": "Stock check + warehouse",
        "turns": [
            {
                "id": "M12a",
                "text": "cek stok Kain Taslan",
                "expect_intent": "query_item_detail|query_warehouse_stock",
            },
            {
                "id": "M12b",
                "text": "di gudang mana saja",
                "expect_intent": "query_warehouse_stock|query_warehouses",
            },
        ],
    },
    {
        "group": "M13",
        "desc": "Calc chain: avg sell -> avg buy -> sum stock",
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
        "desc": "Dashboard + overdue drill",
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
        "desc": "Low stock rekapan (bug we fixed)",
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
        "desc": "Customer list + count",
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
        "desc": "Vendor list + count",
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
        "desc": "Sales summary + payment check",
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
        "desc": "Purchase summary + payment check",
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
        "desc": "Financial health: bank -> AR -> AP",
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

# Known real entities in grapgrap tenant for coherence checking
KNOWN_ENTITIES = {
    "items": [
        "poloshirt",
        "kain taslan",
        "manset",
        "kerah",
        "label",
        "benang jahit",
        "lacoste",
    ],
    "customers": ["sintia"],
    "vendors": ["pt"],
    "banks": ["bca", "kas"],
}


def check_hallucination(text):
    t = text.lower()
    return [kw for kw in HALLUCINATION_KEYWORDS if kw in t]


def check_coherence(prev_text, curr_text, group_desc):
    """Check if follow-up response is coherent with previous context."""
    issues = []
    pt = (prev_text or "").lower()
    ct = (curr_text or "").lower()

    # If prev mentioned specific entities, follow-up should reference same domain
    if "piutang" in pt and "hutang" in ct and "hutang" not in group_desc.lower():
        issues.append("DOMAIN_LEAK: prev=piutang, curr mentions hutang")
    if "hutang" in pt and "piutang" in ct and "piutang" not in group_desc.lower():
        issues.append("DOMAIN_LEAK: prev=hutang, curr mentions piutang")

    return issues


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
        "%s/api/auth/login" % BASE,
        json={"email": EMAIL, "password": PASSWORD, "tenant_slug": TENANT},
    )
    return r.json()["data"]["access_token"]


async def send_message(client, token, text, conversation_id):
    start = time.time()
    try:
        r = await client.post(
            "%s/api/v3/chat/message" % BASE,
            headers={"Authorization": "Bearer %s" % token},
            json={"text": text, "conversation_id": conversation_id},
            timeout=TIMEOUT,
        )
        latency = int((time.time() - start) * 1000)
        if r.status_code != 200:
            return {
                "error": "HTTP %s: %s" % (r.status_code, r.text[:100]),
                "latency_ms": latency,
            }
        data = r.json()
        tool_calls = data.get("tool_calls") or []
        return {
            "text": data.get("text", "")[:500],
            "full_text": data.get("text", ""),
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


async def main():
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    total_turns = sum(len(g["turns"]) for g in MULTI_TURN_GROUPS)
    print("Discovery Multi-Turn -- %s" % ts)
    print("Target: %s | Tenant: %s" % (BASE, TENANT))
    print(
        "%d turns across %d conversation groups\n"
        % (total_turns, len(MULTI_TURN_GROUPS))
    )

    async with httpx.AsyncClient(timeout=30.0) as client:
        token = await login(client)
        print("Logged in OK\n")

        results = []
        group_results = []

        for group in MULTI_TURN_GROUPS:
            conv_id = "disc-m-%s-%s" % (group["group"], int(time.time()))
            print("\n%s" % SEP)
            print(
                "Group %s: %s (conv=%s)" % (group["group"], group["desc"], conv_id[:40])
            )
            print(SEP)

            prev_text = None
            group_pass = True
            group_detail = {"group": group["group"], "desc": group["desc"], "turns": []}

            for i, turn in enumerate(group["turns"]):
                r = await send_message(client, token, turn["text"], conv_id)

                issues = []
                if "error" in r:
                    issues.append("ERROR: %s" % r["error"][:80])
                else:
                    # Intent check
                    actual_intent = r.get("intent", "")
                    if actual_intent and not intent_matches(
                        actual_intent, turn["expect_intent"]
                    ):
                        issues.append(
                            "INTENT: got=%s expected=%s"
                            % (actual_intent, turn["expect_intent"])
                        )

                    # Hallucination check
                    halluc = check_hallucination(r.get("full_text", ""))
                    if halluc:
                        issues.append("HALLUCINATION: %s" % halluc)

                    # Coherence check (for follow-up turns)
                    if i > 0 and prev_text:
                        coherence = check_coherence(
                            prev_text, r.get("full_text", ""), group["desc"]
                        )
                        issues.extend(coherence)

                    # Latency check
                    lat = r.get("latency_ms", 0)
                    if lat > 10000:
                        issues.append("SLOW: %dms" % lat)

                    # Empty response
                    if r.get("text", "").strip() == "":
                        issues.append("EMPTY_RESPONSE")

                    # Check if bot asked clarification when it shouldn't (for specific queries)
                    txt = r.get("text", "").lower()
                    if "sebutkan" in txt and "nama" in txt and i == 0:
                        # First turn shouldn't need clarification if entity is in the query
                        if any(
                            e in turn["text"].lower()
                            for e in ["sintia", "poloshirt", "kain taslan"]
                        ):
                            issues.append(
                                "UNNECESSARY_CLARIFICATION: entity was in query"
                            )

                    prev_text = r.get("full_text", "")

                status = "FAIL" if issues else "PASS"
                if issues:
                    group_pass = False

                lat_str = "%5dms" % r.get("latency_ms", 0)
                intent_str = r.get("intent", "-")[:30]
                model_str = (r.get("model_used", "?") or "?")[:25]
                resp_preview = r.get("text", "")[:120].replace("\n", " ")

                turn_result = {
                    "id": turn["id"],
                    "text": turn["text"],
                    "status": status,
                    "latency_ms": r.get("latency_ms", 0),
                    "intent": intent_str,
                    "model": model_str,
                    "endpoint": r.get("endpoint", ""),
                    "issues": issues,
                    "response_preview": resp_preview,
                    "full_response": r.get("full_text", "")[:500],
                }
                results.append(turn_result)
                group_detail["turns"].append(turn_result)

                icon = "PASS" if status == "PASS" else "FAIL"
                print(
                    "  %s %s [%s] [%s] %s"
                    % (icon, turn["id"], lat_str, model_str, turn["text"][:45])
                )
                print("       -> %s" % resp_preview[:100])
                if issues:
                    for iss in issues:
                        print("       !! %s" % iss)

                # Rate limit - slightly longer between turns to avoid gateway stress
                await asyncio.sleep(0.5)

            g_icon = "PASS" if group_pass else "FAIL"
            print("  --- Group %s: %s ---" % (group["group"], g_icon))
            group_detail["status"] = "PASS" if group_pass else "FAIL"
            group_results.append(group_detail)

            # Pause between groups
            await asyncio.sleep(1.0)

        # SUMMARY
        print("\n%s" % SEP)
        print("SUMMARY")
        print("%s\n" % SEP)

        total = len(results)
        passed = sum(1 for r in results if r["status"] == "PASS")
        failed = total - passed
        groups_passed = sum(1 for g in group_results if g["status"] == "PASS")

        latencies = [r["latency_ms"] for r in results if r.get("latency_ms")]
        avg_lat = sum(latencies) / len(latencies) if latencies else 0
        max_lat = max(latencies) if latencies else 0
        sorted_lat = sorted(latencies)
        p90_lat = sorted_lat[int(len(sorted_lat) * 0.9)] if sorted_lat else 0

        print("Turns:      %d" % total)
        print("Passed:     %d (%d%%)" % (passed, passed * 100 // total if total else 0))
        print("Failed:     %d (%d%%)" % (failed, failed * 100 // total if total else 0))
        print("Groups:     %d/%d passed" % (groups_passed, len(group_results)))
        print("Avg lat:    %dms" % avg_lat)
        print("P90 lat:    %dms" % p90_lat)
        print("Max lat:    %dms" % max_lat)

        # Categorize failures
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
        coherence_fails = [
            r for r in results if any("DOMAIN_LEAK" in i for i in r.get("issues", []))
        ]
        clarify_fails = [
            r
            for r in results
            if any("UNNECESSARY_CLARIFICATION" in i for i in r.get("issues", []))
        ]

        if intent_fails:
            print("\nINTENT MISMATCHES (%d):" % len(intent_fails))
            for r in intent_fails:
                iss = [i for i in r["issues"] if "INTENT" in i][0]
                print("   %s: %s -> %s" % (r["id"], r["text"][:50], iss))

        if halluc_fails:
            print("\nHALLUCINATIONS (%d):" % len(halluc_fails))
            for r in halluc_fails:
                iss = [i for i in r["issues"] if "HALLUCINATION" in i][0]
                print("   %s: %s -> %s" % (r["id"], r["text"][:50], iss))

        if error_fails:
            print("\nERRORS (%d):" % len(error_fails))
            for r in error_fails:
                iss = [i for i in r["issues"] if "ERROR" in i][0]
                print("   %s: %s -> %s" % (r["id"], r["text"][:50], iss))

        if slow_fails:
            print("\nSLOW >10s (%d):" % len(slow_fails))
            for r in slow_fails:
                print("   %s: %s -> %dms" % (r["id"], r["text"][:50], r["latency_ms"]))

        if coherence_fails:
            print("\nDOMAIN LEAKS (%d):" % len(coherence_fails))
            for r in coherence_fails:
                iss = [i for i in r["issues"] if "DOMAIN_LEAK" in i][0]
                print("   %s: %s -> %s" % (r["id"], r["text"][:50], iss))

        if clarify_fails:
            print("\nUNNECESSARY CLARIFICATIONS (%d):" % len(clarify_fails))
            for r in clarify_fails:
                print("   %s: %s" % (r["id"], r["text"][:50]))

        # Failed groups detail
        failed_groups = [g for g in group_results if g["status"] == "FAIL"]
        if failed_groups:
            print("\nFAILED GROUPS DETAIL:")
            for g in failed_groups:
                print("\n  Group %s (%s):" % (g["group"], g["desc"]))
                for t in g["turns"]:
                    icon = "ok" if t["status"] == "PASS" else "!!"
                    print("    [%s] %s: %s" % (icon, t["id"], t["text"][:50]))
                    print("         -> %s" % t["response_preview"][:100])
                    if t["issues"]:
                        for iss in t["issues"]:
                            print("         !! %s" % iss)

        # Save JSON
        out_path = "/root/milkyhoop-dev/backend/api_gateway/tests/chat/discovery_multi_results.json"
        with open(out_path, "w") as f:
            json.dump(
                {
                    "timestamp": datetime.now().isoformat(),
                    "total_turns": total,
                    "passed": passed,
                    "failed": failed,
                    "groups_total": len(group_results),
                    "groups_passed": groups_passed,
                    "avg_latency_ms": round(avg_lat),
                    "p90_latency_ms": p90_lat,
                    "max_latency_ms": max_lat,
                    "results": results,
                    "group_results": group_results,
                },
                f,
                indent=2,
                ensure_ascii=False,
            )
        print("\nFull results: %s" % out_path)


if __name__ == "__main__":
    asyncio.run(main())
