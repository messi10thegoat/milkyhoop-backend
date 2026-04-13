"""
100-Query Multi-Turn Stress Test v2
20 groups x 5 turns = 100 queries
3 tiers: core flows, natural language stress, conversation edge cases

DIAGNOSTIC: log everything, fix nothing.
"""
import httpx, json, uuid, time, asyncio

BASE = "http://localhost:8001"
EMAIL = "grapmanado@gmail.com"
PASSWORD = "grapgrap007"

GROUPS = [
    # TIER 1: CORE FLOWS (8 groups, 40 queries)
    ("T1_01_ar_deep", "core", [
        ("piutang saya berapa?", ["piutang", "Rp"], ["barang"], 8000),
        ("rincian per faktur dong", ["faktur", "INV", "tabel", "|"], ["barang"], 8000),
        ("yang belum dibayar mana aja?", ["belum", "Rp", "unpaid"], ["barang"], 8000),
        ("ada yang jatuh tempo minggu ini?", ["jatuh tempo", "overdue", "belum ada", "tidak"], [], 8000),
        ("tampilkan dalam tabel", ["tabel", "|"], [], 8000),
    ]),
    ("T1_02_ap_deep", "core", [
        ("total hutang kita berapa ya?", ["hutang", "Rp"], [], 8000),
        ("rincian per faktur pembelian", ["faktur", "PB", "tagihan", "tabel"], ["piutang"], 8000),
        ("yang overdue mana?", ["overdue", "jatuh tempo", "belum ada", "tidak"], [], 8000),
        ("urutkan dari yang terbesar", ["Rp", "tabel", "|"], [], 8000),
        ("total yang belum dibayar berapa?", ["total", "Rp"], [], 8000),
    ]),
    ("T1_03_items_stok", "core", [
        ("ada berapa barang yang dijual?", ["barang", "item", "aktif", "produk"], [], 8000),
        ("yang paling mahal apa?", ["Rp", "harga", "termahal", "paling"], [], 8000),
        ("ada yang stoknya habis?", ["stok", "habis", "kosong", "tidak ada", "0"], [], 8000),
        ("daftar kategori barang", ["kategori", "category"], [], 8000),
        ("total nilai stok semua barang?", ["stok", "Rp", "total", "nilai"], [], 8000),
    ]),
    ("T1_04_kas_bank", "core", [
        ("saldo semua rekening berapa?", ["saldo", "rekening", "Rp", "bank"], [], 8000),
        ("saldo BCA berapa?", ["BCA", "Rp", "saldo"], ["barang"], 8000),
        ("rekening mana yang paling banyak?", ["Rp", "terbesar", "paling", "rekening"], [], 8000),
        ("daftar rekening aktif", ["rekening", "bank", "aktif"], [], 8000),
        ("total uang kita di semua bank?", ["total", "Rp", "bank"], [], 8000),
    ]),
    ("T1_05_expense", "core", [
        ("total pengeluaran bulan ini?", ["pengeluaran", "biaya", "Rp"], [], 8000),
        ("pengeluaran terbesar untuk apa?", ["pengeluaran", "biaya", "Rp", "akun"], [], 8000),
        ("daftar biaya bulan ini", ["biaya", "pengeluaran", "Rp"], [], 8000),
        ("ada berapa transaksi biaya?", ["biaya", "transaksi", "pengeluaran"], [], 8000),
        ("ringkasan pengeluaran", ["pengeluaran", "biaya", "ringkasan", "Rp"], [], 8000),
    ]),
    ("T1_06_crud_customer", "core", [
        ("daftar pelanggan siapa aja?", ["pelanggan", "customer"], [], 8000),
        ("ada berapa pelanggan aktif?", ["pelanggan", "aktif"], [], 8000),
        ("tambah pelanggan baru Budi Santoso", ["Budi", "konfirmasi", "Betul", "pelanggan"], [], 10000),
        ("detail pelanggan Sintia", ["Sintia", "telepon", "alamat", "email", "pelanggan"], [], 8000),
        ("piutang Sintia berapa?", ["Sintia", "Rp", "piutang"], [], 8000),
    ]),
    ("T1_07_crud_vendor", "core", [
        ("daftar vendor", ["vendor", "pemasok"], [], 8000),
        ("berapa vendor aktif?", ["vendor", "aktif"], [], 8000),
        ("buat vendor baru PT Maju Bersama", ["Maju", "vendor", "konfirmasi", "Betul", "barang", "jasa"], [], 10000),
        ("detail vendor PT Bahagia", ["Bahagia", "vendor", "telepon", "alamat"], [], 8000),
        ("hutang ke PT Bahagia berapa?", ["Bahagia", "Rp", "hutang"], [], 8000),
    ]),
    ("T1_08_report_basic", "core", [
        ("laba rugi bulan ini gimana?", ["laba", "rugi", "pendapatan", "beban", "Rp"], [], 15000),
        ("neraca saldo?", ["neraca", "trial balance", "debit", "kredit", "saldo"], [], 15000),
        ("posisi keuangan kita sehat ga?", ["keuangan", "Rp"], [], 15000),
        ("arus kas bulan ini?", ["arus kas", "cash flow", "Rp"], [], 15000),
        ("ringkasan dashboard", ["ringkasan", "Rp", "piutang", "hutang", "kas", "penjualan"], [], 15000),
    ]),

    # TIER 2: NATURAL LANGUAGE STRESS (6 groups, 30 queries)
    ("T2_09_prefix_noise", "natural", [
        ("eh btw piutang berapa ya?", ["piutang", "Rp"], [], 8000),
        ("hmm kalau hutangnya gimana?", ["hutang", "Rp"], [], 8000),
        ("ok terus daftar barang dong", ["barang", "item", "produk"], ["piutang", "hutang"], 8000),
        ("oh iya, saldo kas masih ada?", ["saldo", "kas", "Rp"], [], 8000),
        ("nah gw mau bikin faktur nih", ["faktur", "invoice", "pelanggan", "penjualan", "konfirmasi"], [], 10000),
    ]),
    ("T2_10_slang_casual", "natural", [
        ("duit kita tinggal berapa sih?", ["saldo", "kas", "Rp", "bank", "rekening"], [], 8000),
        ("ada yang nunggak ga?", ["piutang", "overdue", "jatuh tempo", "Rp", "belum"], [], 8000),
        ("siapa yang belum bayar?", ["pelanggan", "piutang", "belum", "Rp"], [], 8000),
        ("vendor mana yang kita belum bayar?", ["vendor", "hutang", "Rp", "belum"], [], 8000),
        ("bulan ini untung ga sih?", ["laba", "rugi", "Rp", "pendapatan"], [], 15000),
    ]),
    ("T2_11_abbreviation", "natural", [
        ("brp total piutang?", ["piutang", "Rp"], [], 8000),
        ("cek stok poloshirt", ["stok", "poloshirt", "Polo"], [], 8000),
        ("brp vendor aktif?", ["vendor", "aktif"], [], 8000),
        ("tgl jatuh tempo faktur?", ["jatuh tempo", "faktur", "tanggal"], [], 8000),
        ("info pelanggan sintia dong", ["Sintia", "pelanggan", "telepon", "alamat"], [], 8000),
    ]),
    ("T2_12_mixed_indo_english", "natural", [
        ("show me outstanding receivable", ["piutang", "Rp", "outstanding", "receivable"], [], 8000),
        ("total payable berapa?", ["hutang", "Rp", "payable", "outstanding"], [], 8000),
        ("list semua items", ["barang", "item", "produk"], [], 8000),
        ("cash balance berapa?", ["saldo", "kas", "Rp", "cash", "balance"], [], 8000),
        ("create new customer Ahmad", ["Ahmad", "pelanggan", "customer", "konfirmasi"], [], 10000),
    ]),
    ("T2_13_incomplete_sentence", "natural", [
        ("piutang?", ["piutang", "Rp"], [], 8000),
        ("hutang?", ["hutang", "Rp"], [], 8000),
        ("stok?", ["stok", "barang", "item"], [], 8000),
        ("vendor?", ["vendor", "pemasok"], [], 8000),
        ("kas?", ["kas", "saldo", "Rp", "bank"], [], 8000),
    ]),
    ("T2_14_long_natural", "natural", [
        ("saya mau tahu berapa total piutang yang belum dibayar oleh semua pelanggan saya saat ini", ["piutang", "Rp"], [], 8000),
        ("tolong carikan vendor yang kita punya hutang paling besar supaya bisa saya prioritaskan pembayaran", ["vendor", "hutang", "Rp", "terbesar"], [], 10000),
        ("bisa bantu saya lihat semua barang yang stoknya sudah habis atau tinggal sedikit di gudang?", ["stok", "habis", "rendah", "barang", "gudang"], [], 8000),
        ("saya ingin tahu apakah ada tagihan yang sudah jatuh tempo dan belum kita bayar ke vendor", ["jatuh tempo", "overdue", "tagihan", "belum"], [], 8000),
        ("tolong buatkan faktur penjualan untuk pelanggan Sintia Runtuwene", ["Sintia", "faktur", "penjualan", "invoice", "konfirmasi"], [], 10000),
    ]),

    # TIER 3: CONVERSATION EDGE CASES (6 groups, 30 queries)
    ("T3_15_correction_midflow", "edge", [
        ("buat faktur untuk Sintia", ["Sintia", "faktur", "konfirmasi", "penjualan"], [], 10000),
        ("eh salah, bukan Sintia tapi Budi", ["Budi", "faktur", "konfirmasi", "penjualan", "ganti"], [], 10000),
        ("harganya 150 ribu per pcs", ["150", "harga", "Rp"], [], 8000),
        ("eh bukan, 200 ribu deng", ["200", "harga", "Rp", "ganti", "ubah"], [], 8000),
        ("ya sudah, batal aja", ["batal", "cancel", "dibatalkan", "oke"], [], 5000),
    ]),
    ("T3_16_topic_jump", "edge", [
        ("piutang berapa?", ["piutang", "Rp"], [], 8000),
        ("oh iya ada vendor baru mau didaftarin", ["vendor", "konfirmasi", "nama"], [], 10000),
        ("namanya PT Citra Mandiri", ["Citra", "vendor", "konfirmasi"], [], 10000),
        ("btw barang terlaris apa ya?", ["barang", "terlaris", "laku"], [], 8000),
        ("ok makasih ya", ["sama-sama", "terima kasih", "senang", "membantu"], [], 5000),
    ]),
    ("T3_17_long_pronoun_chain", "edge", [
        ("hutang kita berapa?", ["hutang", "Rp"], [], 8000),
        ("ke siapa aja?", ["vendor", "PT", "pemasok", "tagihan", "hutang", "Rp"], [], 8000),
        ("yang paling besar?", ["Rp", "terbesar", "paling", "hutang", "vendor"], [], 8000),
        ("detailnya?", ["detail", "vendor", "faktur", "tagihan", "Rp"], [], 8000),
        ("bisa dibayar sekarang?", ["bayar", "Rp", "rekening", "tagihan", "faktur"], [], 10000),
    ]),
    ("T3_18_cancel_restart", "edge", [
        ("buat vendor baru", ["vendor", "nama", "konfirmasi"], [], 10000),
        ("batal", ["batal", "cancel", "dibatalkan", "oke"], [], 5000),
        ("hutang berapa?", ["hutang", "Rp"], [], 8000),
        ("buat pelanggan baru Dewi Lestari", ["Dewi", "pelanggan", "konfirmasi", "Betul"], [], 10000),
        ("batal juga deh", ["batal", "cancel", "dibatalkan", "oke"], [], 5000),
    ]),
    ("T3_19_ambiguous_intent", "edge", [
        ("gimana kondisi keuangan?", ["keuangan", "Rp", "piutang", "hutang", "kas", "saldo"], [], 15000),
        ("ada masalah ga?", ["masalah", "aman", "overdue", "jatuh tempo", "baik", "tidak"], [], 15000),
        ("apa yang harus diprioritaskan?", ["prioritas", "bayar", "tagih", "Rp", "saran"], [], 15000),
        ("rekap dong", ["rekap", "ringkasan", "Rp", "tabel"], [], 10000),
        ("update terbaru?", ["update", "terbaru", "Rp", "hari ini"], [], 15000),
    ]),
    ("T3_20_double_intent", "edge", [
        ("cek piutang sekalian hutang", ["piutang", "hutang", "Rp"], [], 10000),
        ("buat faktur sekaligus cek stok poloshirt", ["faktur", "stok", "poloshirt", "penjualan"], [], 10000),
        ("siapa pelanggan terbesar dan vendor terbesar?", ["pelanggan", "vendor", "Rp", "terbesar"], [], 10000),
        ("daftar barang sama daftar vendor", ["barang", "vendor", "item", "pemasok"], [], 10000),
        ("hapus barang test sekalian tambah barang baru", ["hapus", "tambah", "barang", "konfirmasi"], [], 10000),
    ]),
]

async def run_test():
    async with httpx.AsyncClient(timeout=60) as client:
        login = await client.post(f"{BASE}/api/auth/login", json={
            "email": EMAIL, "password": PASSWORD
        })
        token = login.json()["data"]["access_token"]
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

        results = []
        tier_stats = {"core": {"pass": 0, "fail": 0, "warn": 0, "slow": 0},
                      "natural": {"pass": 0, "fail": 0, "warn": 0, "slow": 0},
                      "edge": {"pass": 0, "fail": 0, "warn": 0, "slow": 0}}

        for group_name, tier, turns in GROUPS:
            conv_id = str(uuid.uuid4())
            session_id = conv_id
            print(f"\n{'='*70}")
            print(f"GROUP: {group_name} [{tier}] (conv={conv_id[:8]})")
            print(f"{'='*70}")

            for turn_idx, (query, must_contain, must_not_contain, max_lat) in enumerate(turns):
                start = time.time()
                try:
                    resp = await client.post(f"{BASE}/api/v3/chat/message", json={
                        "conversation_id": conv_id,
                        "session_id": session_id,
                        "text": query
                    }, headers=headers)
                    elapsed = int((time.time() - start) * 1000)
                    data = resp.json()

                    response_text = data.get("text", data.get("response", ""))
                    if not response_text and "data" in data:
                        response_text = str(data["data"])
                    response_lower = response_text.lower()

                    contain_pass = True
                    if must_contain:
                        contain_pass = any(kw.lower() in response_lower for kw in must_contain)

                    not_contain_pass = True
                    bad_keyword = ""
                    for kw in must_not_contain:
                        if kw.lower() in response_lower:
                            not_contain_pass = False
                            bad_keyword = kw
                            break

                    latency_pass = elapsed <= max_lat

                    if contain_pass and not_contain_pass and latency_pass:
                        status = "PASS"
                        tier_stats[tier]["pass"] += 1
                    elif not not_contain_pass:
                        status = f"FAIL(has '{bad_keyword}')"
                        tier_stats[tier]["fail"] += 1
                    elif not contain_pass:
                        status = "WARN(missing kw)"
                        tier_stats[tier]["warn"] += 1
                    elif not latency_pass:
                        status = f"SLOW({elapsed}ms>{max_lat}ms)"
                        tier_stats[tier]["slow"] += 1

                    lat_icon = "\U0001f7e2" if elapsed < 3000 else "\U0001f7e1" if elapsed < 8000 else "\U0001f534"

                    print(f"  T{turn_idx+1} [{status:25s}] {lat_icon} {elapsed:5d}ms Q: {query[:60]}")
                    if status != "PASS":
                        preview = response_text[:150].replace("\n", " ")
                        print(f"      -> {preview}")

                    results.append({
                        "group": group_name, "tier": tier, "turn": turn_idx + 1,
                        "query": query, "status": status,
                        "latency_ms": elapsed,
                        "response_preview": response_text[:300],
                        "model": data.get("model_used", "?"),
                    })

                except Exception as e:
                    elapsed = int((time.time() - start) * 1000)
                    print(f"  T{turn_idx+1} [ERROR                   ] \U0001f534 {elapsed:5d}ms Q: {query[:60]}")
                    print(f"      -> {str(e)[:100]}")
                    tier_stats[tier]["fail"] += 1
                    results.append({
                        "group": group_name, "tier": tier, "turn": turn_idx + 1,
                        "query": query, "status": f"ERROR: {str(e)[:80]}",
                        "latency_ms": elapsed, "response_preview": "", "model": "error",
                    })

                await asyncio.sleep(1.5)
            await asyncio.sleep(2)

        # SUMMARY
        total = len(results)
        total_pass = sum(1 for r in results if r["status"] == "PASS")
        total_fail = sum(1 for r in results if r["status"].startswith("FAIL") or r["status"].startswith("ERROR"))
        total_warn = sum(1 for r in results if r["status"].startswith("WARN"))
        total_slow = sum(1 for r in results if r["status"].startswith("SLOW"))

        print(f"\n{'='*70}")
        print(f"STRESS TEST RESULTS - 100 QUERIES")
        print(f"{'='*70}")
        print(f"Total:   {total}")
        print(f"PASS:    {total_pass} ({total_pass/total*100:.0f}%)")
        print(f"FAIL:    {total_fail}")
        print(f"WARN:    {total_warn}")
        print(f"SLOW:    {total_slow}")

        print(f"\nPer Tier:")
        for tier_name in ["core", "natural", "edge"]:
            ts = tier_stats[tier_name]
            tier_total = ts["pass"] + ts["fail"] + ts["warn"] + ts["slow"]
            print(f"  {tier_name:8s}: {ts['pass']}/{tier_total} pass "
                  f"({ts['pass']/tier_total*100:.0f}%) | "
                  f"fail={ts['fail']} warn={ts['warn']} slow={ts['slow']}")

        lats = [r["latency_ms"] for r in results if "ERROR" not in r["status"]]
        if lats:
            print(f"\nLatency:")
            print(f"  Average: {sum(lats)/len(lats):.0f}ms")
            print(f"  Pipeline (<5s):  {sum(1 for l in lats if l < 5000)}")
            print(f"  Slow (5-15s):    {sum(1 for l in lats if 5000 <= l < 15000)}")
            print(f"  Agent loop (>15s): {sum(1 for l in lats if l >= 15000)}")
            print(f"  Fastest: {min(lats)}ms")
            print(f"  Slowest: {max(lats)}ms")

        print(f"\nPer-Group:")
        for group_name, tier, _ in GROUPS:
            g = [r for r in results if r["group"] == group_name]
            g_pass = sum(1 for r in g if r["status"] == "PASS")
            g_avg = sum(r["latency_ms"] for r in g) / max(len(g), 1)
            icon = "OK" if g_pass == len(g) else "WARN" if g_pass >= len(g) * 0.6 else "FAIL"
            print(f"  {icon} {group_name} [{tier}]: {g_pass}/{len(g)} pass, avg {g_avg:.0f}ms")

        failures = [r for r in results if r["status"] != "PASS"]
        if failures:
            print(f"\n{'='*70}")
            print(f"FAILURE DETAILS ({len(failures)} non-PASS)")
            print(f"{'='*70}")
            for r in failures:
                print(f"\n  [{r['group']}] T{r['turn']} [{r['tier']}] {r['status']}")
                print(f"    Q: {r['query']}")
                print(f"    A: {r['response_preview'][:200]}")

        print(f"\n{'='*70}")
        print(f"PATTERN ANALYSIS")
        print(f"{'='*70}")

        fail_patterns = {}
        for r in failures:
            if r["status"].startswith("FAIL"):
                pattern = "wrong_content"
            elif r["status"].startswith("WARN"):
                pattern = "missing_keyword"
            elif r["status"].startswith("SLOW"):
                pattern = "slow_latency"
            elif r["status"].startswith("ERROR"):
                pattern = "error"
            else:
                pattern = "other"
            fail_patterns.setdefault(pattern, []).append(r)

        for pattern, items in fail_patterns.items():
            print(f"\n  {pattern} ({len(items)} cases):")
            for r in items:
                print(f"    - [{r['group']}] T{r['turn']}: {r['query'][:50]}")

        agent_loop = [r for r in results if r["latency_ms"] > 8000]
        if agent_loop:
            print(f"\n  Agent loop detected ({len(agent_loop)} queries >8s):")
            for r in agent_loop:
                print(f"    - [{r['group']}] T{r['turn']} {r['latency_ms']}ms: {r['query'][:50]}")

        report = {
            "summary": {
                "total": total, "pass": total_pass, "fail": total_fail,
                "warn": total_warn, "slow": total_slow,
                "pass_rate": round(total_pass/total*100, 1),
            },
            "tier_stats": tier_stats,
            "results": results,
        }
        with open("/root/milkyhoop-dev/backend/docs/reports/stress-test-100-v2.json", "w") as f:
            json.dump(report, f, indent=2, ensure_ascii=False)
        print(f"\nSaved to docs/reports/stress-test-100-v2.json")

asyncio.run(run_test())
