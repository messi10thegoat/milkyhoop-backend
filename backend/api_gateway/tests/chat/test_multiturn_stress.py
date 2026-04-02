import httpx, json, uuid, time, asyncio, sys

BASE = "http://localhost:8001"
EMAIL = "grapmanado@gmail.com"
PASSWORD = "grapgrap007"

GROUPS = [
    ("G01_piutang_basic", [
        ("berapa total piutang saya?", ["piutang", "Rp"], ["barang", "stok"]),
        ("dari pelanggan siapa aja?", ["pelanggan", "customer"], ["barang", "sebutkan nama"]),
        ("yang paling besar siapa?", ["Rp"], ["barang", "sebutkan nama"]),
        ("ada yang sudah jatuh tempo?", ["jatuh tempo", "overdue", "belum ada"], ["barang"]),
        ("oke terima kasih", ["sama-sama", "terima kasih", "senang", "membantu"], []),
    ]),
    ("G02_piutang_drilldown", [
        ("piutang saya berapa ya?", ["piutang", "Rp"], []),
        ("bisa tampilkan per faktur?", ["faktur", "INV", "tabel"], ["barang"]),
        ("yang belum dibayar aja", ["belum", "Rp"], ["barang"]),
        ("tolong bikin dalam bentuk tabel", ["tabel", "|"], ["barang"]),
        ("total keseluruhannya berapa?", ["total", "Rp"], []),
    ]),
    ("G03_customer_detail", [
        ("daftar pelanggan saya siapa aja?", ["pelanggan", "customer"], ["barang"]),
        ("ada berapa pelanggan aktif?", ["pelanggan", "aktif"], []),
        ("piutang Sintia berapa?", ["Sintia", "Rp"], ["barang"]),
        ("detail lengkap pelanggan tersebut", ["Sintia", "telepon", "alamat", "email"], ["barang", "sebutkan nama"]),
        ("ada transaksi apa aja dari dia?", ["faktur", "invoice", "transaksi"], ["barang"]),
    ]),
    ("G04_invoice_specific", [
        ("daftar faktur penjualan bulan ini", ["faktur", "INV"], []),
        ("ada yang belum dibayar?", ["belum", "Rp", "unpaid"], ["barang"]),
        ("yang jatuh tempo kapan aja?", ["jatuh tempo", "tanggal"], ["barang"]),
        ("tampilkan dalam tabel", ["tabel", "|"], []),
        ("ringkasan penjualan bulan ini berapa?", ["penjualan", "Rp", "total"], []),
    ]),
    ("G05_hutang_basic", [
        ("hutang saya total berapa?", ["hutang", "Rp"], ["piutang"]),
        ("ke vendor siapa aja?", ["vendor", "pemasok", "PT"], ["barang", "sebutkan nama"]),
        ("yang paling besar ke siapa?", ["Rp", "vendor", "PT"], ["barang"]),
        ("ada yang jatuh tempo minggu ini?", ["jatuh tempo", "belum ada", "minggu"], []),
        ("terima kasih ya", ["sama-sama", "terima kasih", "senang"], []),
    ]),
    ("G06_hutang_drilldown", [
        ("berapa total hutang saat ini?", ["hutang", "Rp"], []),
        ("rincian per faktur dong", ["faktur", "PB", "tabel"], ["barang"]),
        ("yang belum dibayar saja", ["belum", "Rp", "unpaid"], []),
        ("urutkan dari yang terbesar", ["Rp", "tabel", "|"], []),
        ("total yang belum bayar berapa?", ["total", "Rp"], []),
    ]),
    ("G07_vendor_detail", [
        ("daftar vendor saya", ["vendor", "pemasok", "PT"], []),
        ("berapa vendor aktif?", ["vendor", "aktif"], []),
        ("hutang ke PT Bahagia Sejahtera berapa?", ["Bahagia", "Rp"], ["barang"]),
        ("detail vendor tersebut", ["Bahagia", "telepon", "alamat"], ["barang", "sebutkan nama"]),
        ("faktur apa aja dari mereka?", ["faktur", "PB", "tagihan"], ["barang"]),
    ]),
    ("G08_bills_specific", [
        ("daftar tagihan pembelian", ["tagihan", "faktur", "PB"], []),
        ("mana yang belum lunas?", ["belum", "Rp", "lunas"], ["barang"]),
        ("ada berapa tagihan aktif?", ["tagihan", "aktif"], []),
        ("tampilkan tabelnya", ["tabel", "|", "rangkuman", "data"], []),
        ("total pembelian bulan ini berapa?", ["pembelian", "Rp", "total"], []),
    ]),
    ("G09_items_overview", [
        ("ada berapa barang yang saya jual?", ["barang", "item", "aktif"], []),
        ("yang paling mahal apa?", ["Rp", "harga"], []),
        ("rata-rata harga jualnya berapa?", ["rata-rata", "Rp"], []),
        ("ada yang stoknya habis?", ["stok", "habis", "kosong", "tidak ada"], []),
        ("total nilai stok semua barang berapa?", ["stok", "Rp", "total"], []),
    ]),
    ("G10_items_detail", [
        ("daftar barang yang harganya di atas 100 ribu", ["barang", "Rp", "100"], []),
        ("ranking barang berdasarkan stok", ["ranking", "stok", "tabel", "|"], []),
        ("barang terlaris apa?", ["terlaris", "laku", "produk"], []),
        ("margin keuntungan per barang gimana?", ["margin", "keuntungan", "Rp"], []),
        ("total harga beli semua barang berapa?", ["total", "harga beli", "Rp"], []),
    ]),
    ("G11_stok_check", [
        ("stok di gudang berapa?", ["stok", "gudang"], []),
        ("barang apa yang stoknya paling banyak?", ["stok", "ranking"], []),
        ("ada barang yang stok rendah?", ["stok", "rendah", "low"], []),
        ("daftar kategori barang", ["kategori", "category"], []),
        ("berapa jenis barang yang tidak aktif?", ["tidak aktif", "inactive"], []),
    ]),
    ("G12_reports_basic", [
        ("gimana laba rugi bulan ini?", ["laba", "rugi", "pendapatan", "beban", "Rp"], []),
        ("neracanya gimana?", ["neraca", "aset", "kewajiban", "ekuitas", "Rp"], ["barang"]),
        ("arus kas bulan ini?", ["arus kas", "cash flow", "Rp"], ["barang"]),
        ("neraca saldo?", ["neraca saldo", "trial balance", "debit", "kredit"], []),
        ("posisi keuangan kita sehat ga?", ["keuangan", "Rp"], []),
    ]),
    ("G13_kas_bank", [
        ("saldo semua rekening berapa?", ["saldo", "rekening", "Rp"], []),
        ("saldo BCA berapa?", ["BCA", "Rp", "saldo"], ["barang"]),
        ("ada transaksi apa aja di BCA bulan ini?", ["transaksi", "BCA"], ["barang"]),
        ("total uang kita di semua bank berapa?", ["total", "Rp", "bank"], []),
        ("aman ga cash flow kita?", ["cash", "aman", "Rp", "kas"], []),
    ]),
    ("G14_expense_analysis", [
        ("total pengeluaran bulan ini berapa?", ["pengeluaran", "biaya", "Rp"], []),
        ("pengeluaran terbesar untuk apa?", ["pengeluaran", "biaya", "akun", "Rp"], []),
        ("daftar biaya bulan ini", ["biaya", "pengeluaran", "Rp"], []),
        ("ada berapa transaksi biaya bulan ini?", ["biaya", "transaksi", "pengeluaran", "jumlah"], []),
        ("biaya untuk listrik berapa?", ["biaya", "Rp"], []),
    ]),
    ("G15_switch_ar_to_ap", [
        ("piutang berapa?", ["piutang", "Rp"], []),
        ("kalau hutang?", ["hutang", "Rp"], ["piutang"]),
        ("mana yang lebih besar?", ["Rp", "piutang", "hutang", "lebih"], []),
        ("daftar barang dong", ["barang", "item"], ["piutang", "hutang"]),
        ("berapa total stok?", ["stok", "total", "Rp"], []),
    ]),
    ("G16_switch_report_to_crud", [
        ("laba rugi bulan ini gimana?", ["laba", "rugi", "Rp"], []),
        ("daftar pelanggan saya", ["pelanggan", "customer"], ["laba", "rugi"]),
        ("tambah pelanggan baru namanya Budi Santoso", ["Budi", "konfirmasi", "Betul", "pelanggan"], []),
        ("daftar vendor", ["vendor", "pemasok"], ["pelanggan"]),
        ("hutang ke vendor paling besar siapa?", ["hutang", "vendor", "Rp"], []),
    ]),
    ("G17_switch_items_to_finance", [
        ("barang terlaris apa?", ["terlaris", "laku", "produk"], []),
        ("margin keuntungan paling tinggi yang mana?", ["margin", "keuntungan", "Rp"], []),
        ("piutang saya berapa?", ["piutang", "Rp"], ["barang", "margin"]),
        ("arus kas bulan ini gimana?", ["arus kas", "cash flow", "Rp"], ["barang"]),
        ("saldo BCA berapa?", ["BCA", "saldo", "Rp"], ["barang"]),
    ]),
    ("G18_pronoun_chain", [
        ("hutang saya berapa?", ["hutang", "Rp"], []),
        ("ke siapa aja?", ["vendor", "PT"], ["barang", "sebutkan nama"]),
        ("yang paling besar?", ["Rp", "terbesar", "paling"], ["barang", "sebutkan nama"]),
        ("bisa dibayar sekarang ga?", ["bayar", "Rp", "tagihan", "faktur"], ["barang"]),
        ("oke nanti aja deh", ["oke", "baik", "siap"], []),
    ]),
    ("G19_short_followup", [
        ("piutang?", ["piutang", "Rp"], []),
        ("detailnya?", ["detail", "faktur", "pelanggan", "Rp"], ["barang", "sebutkan nama"]),
        ("tabelnya?", ["tabel", "|"], ["barang"]),
        ("hutang?", ["hutang", "Rp"], ["piutang"]),
        ("perbandingannya?", ["piutang", "hutang", "Rp"], []),
    ]),
    ("G20_ambiguous_natural", [
        ("gimana kondisi keuangan saya?", ["keuangan", "Rp", "piutang", "hutang", "kas"], []),
        ("ada masalah ga?", ["masalah", "aman", "overdue", "jatuh tempo", "baik"], []),
        ("apa yang harus saya prioritaskan?", ["prioritas", "bayar", "tagih", "Rp"], []),
        ("siapa yang harus saya tagih duluan?", ["pelanggan", "piutang", "tagih", "Rp"], ["barang"]),
        ("ok makasih ya bos", ["sama-sama", "terima kasih", "senang", "bos"], []),
    ]),
]

SEP = "=" * 70

async def run_test():
    async with httpx.AsyncClient(timeout=60) as client:
        login = await client.post(f"{BASE}/api/auth/login", json={"email": EMAIL, "password": PASSWORD, "tenant_slug": "grapgrap"})
        login_data = login.json()
        token = login_data.get("access_token") or login_data["data"]["access_token"]
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

        results = []
        total_pass = 0
        total_fail = 0
        total_warn = 0

        for group_name, turns in GROUPS:
            conv_id = str(uuid.uuid4())
            print(f"\n{SEP}")
            print(f"GROUP: {group_name} (conv={conv_id[:8]})")
            print(SEP)

            for turn_idx, (query, must_contain, must_not_contain) in enumerate(turns):
                start = time.time()
                try:
                    resp = await client.post(f"{BASE}/api/v3/chat/message", json={
                        "conversation_id": conv_id,
                        "text": query
                    }, headers=headers)
                    elapsed = int((time.time() - start) * 1000)
                    data = resp.json()
                    response_text = data.get("text", data.get("response", ""))
                    if not response_text and "data" in data:
                        response_text = str(data["data"])
                    response_lower = response_text.lower()
                    model = data.get("model_used", data.get("model", "?"))

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

                    if contain_pass and not_contain_pass:
                        status = "PASS"
                        total_pass += 1
                    elif not not_contain_pass:
                        status = f"FAIL(has '{bad_keyword}')"
                        total_fail += 1
                    else:
                        status = "WARN(missing keywords)"
                        total_warn += 1

                    lat_rating = "G" if elapsed < 3000 else "Y" if elapsed < 8000 else "R"
                    print(f"  T{turn_idx+1} [{status:25s}] {lat_rating} {elapsed:5d}ms {model:20s} Q: {query}")
                    if status.startswith("FAIL") or status.startswith("WARN"):
                        preview = response_text[:150].replace("\n", " ")
                        print(f"      -> {preview}")

                    results.append({"group": group_name, "turn": turn_idx + 1, "query": query,
                                    "status": status, "latency_ms": elapsed, "model": model,
                                    "response_preview": response_text[:200]})
                except Exception as e:
                    elapsed = int((time.time() - start) * 1000)
                    print(f"  T{turn_idx+1} [ERROR                   ] R {elapsed:5d}ms Q: {query}")
                    print(f"      -> {str(e)[:100]}")
                    total_fail += 1
                    results.append({"group": group_name, "turn": turn_idx + 1, "query": query,
                                    "status": f"ERROR: {str(e)[:80]}", "latency_ms": elapsed,
                                    "model": "error", "response_preview": ""})
                await asyncio.sleep(0.5)
            await asyncio.sleep(1)

        print(f"\n{SEP}")
        print("SUMMARY")
        print(SEP)
        print(f"Total: {len(results)} queries")
        print(f"PASS:  {total_pass}")
        print(f"FAIL:  {total_fail}")
        print(f"WARN:  {total_warn}")
        print(f"Pass rate: {total_pass/len(results)*100:.1f}%")

        latencies = [r["latency_ms"] for r in results if "ERROR" not in r["status"]]
        if latencies:
            avg_lat = sum(latencies) / len(latencies)
            pipeline_lat = [l for l in latencies if l < 5000]
            agent_lat = [l for l in latencies if l >= 5000]
            print(f"\nLatency:")
            print(f"  Average: {avg_lat:.0f}ms")
            print(f"  Pipeline (<5s): {len(pipeline_lat)} queries, avg {sum(pipeline_lat)/max(len(pipeline_lat),1):.0f}ms")
            print(f"  Agent loop (>5s): {len(agent_lat)} queries, avg {sum(agent_lat)/max(len(agent_lat),1):.0f}ms")
            print(f"  Slowest: {max(latencies)}ms")

        print(f"\nPer-Group:")
        for group_name, _ in GROUPS:
            g_results = [r for r in results if r["group"] == group_name]
            g_pass = sum(1 for r in g_results if r["status"] == "PASS")
            g_total = len(g_results)
            g_avg = sum(r["latency_ms"] for r in g_results) / max(g_total, 1)
            icon = "V" if g_pass == g_total else "W" if g_pass >= 3 else "X"
            print(f"  {icon} {group_name}: {g_pass}/{g_total} pass, avg {g_avg:.0f}ms")

        failures = [r for r in results if r["status"] != "PASS"]
        if failures:
            print(f"\nFailed/Warning Details:")
            for r in failures:
                print(f"  [{r['group']}] T{r['turn']} {r['status']}")
                print(f"    Q: {r['query']}")
                print(f"    A: {r['response_preview'][:120]}")

        with open("/root/milkyhoop-dev/backend/docs/multiturn-stress-test-results.json", "w") as f:
            json.dump({"summary": {"pass": total_pass, "fail": total_fail, "warn": total_warn,
                                   "total": len(results), "pass_rate": total_pass/len(results)*100},
                       "results": results}, f, indent=2)
        print(f"\nResults saved to docs/multiturn-stress-test-results.json")

asyncio.run(run_test())
