"""Phase 2 E2E Multi-Turn Test — 32 queries, 8 groups."""
import httpx, json, uuid, time, asyncio

BASE = "http://localhost:8001"
EMAIL = "grapmanado@gmail.com"
PASSWORD = "grapgrap007"

GROUPS = [
    ("G01_ar_flow", [
        ("berapa total piutang saya?", ["piutang","Rp","outstanding"], ["barang","stok"], 5000),
        ("dari pelanggan siapa aja?", ["pelanggan","customer","Sintia"], ["barang","sebutkan nama"], 5000),
        ("yang paling besar siapa?", ["Rp","terbesar","paling"], ["barang","sebutkan nama"], 8000),
        ("tampilkan dalam tabel", ["tabel","|"], ["barang"], 5000),
        ("oke terima kasih", ["sama-sama","terima kasih","senang","membantu"], [], 3000),
    ]),
    ("G02_ap_flow", [
        ("hutang kita total berapa?", ["hutang","Rp","outstanding"], ["piutang"], 5000),
        ("ke vendor siapa aja?", ["vendor","pemasok","PT"], ["barang","sebutkan nama"], 8000),
        ("yang paling besar ke siapa?", ["Rp","vendor","PT","terbesar"], ["barang"], 8000),
        ("bayar yang paling besar", ["bayar","Rp","rekening","konfirmasi","tagihan"], ["barang"], 10000),
        ("oke terima kasih", ["sama-sama","terima kasih","senang"], [], 3000),
    ]),
    ("G03_items_natural", [
        ("ada stok barang apa saja?", ["barang","item","stok","produk"], [], 8000),
        ("yang paling mahal apa?", ["Rp","harga","termahal","paling","mahal"], [], 8000),
        ("rata-rata harga jualnya berapa?", ["rata-rata","Rp","average","harga"], [], 5000),
        ("ada yang stoknya habis?", ["stok","habis","kosong","tidak ada","0"], [], 12000),
    ]),
    ("G04_crud_vendor", [
        ("daftarkan vendor baru PT Sukses Mandiri", ["vendor","Sukses","Mandiri"], [], 8000),
        ("daftar semua vendor", ["vendor"], ["sebutkan nama"], 8000),
    ]),
    ("G05_domain_switch", [
        ("piutang berapa?", ["piutang","Rp"], [], 5000),
        ("kalau hutang?", ["hutang","Rp"], [], 8000),
        ("daftar barang dong", ["barang","item","produk"], ["piutang","hutang"], 5000),
    ]),
    ("G06_natural_keuangan", [
        ("eh, uang kita masih berapa ya?", ["saldo","kas","Rp","bank","rekening"], [], 8000),
        ("ok, kalau piutang gimana?", ["piutang","Rp"], [], 5000),
        ("terus hutang gw berapa?", ["hutang","Rp"], [], 5000),
    ]),
    ("G07_pronoun_chain", [
        ("hutang saya berapa?", ["hutang","Rp"], [], 5000),
        ("ke siapa aja?", ["vendor","PT","pemasok"], ["barang","sebutkan nama"], 8000),
        ("yang paling besar?", ["Rp","terbesar","paling"], ["barang","sebutkan nama"], 8000),
    ]),
    ("G08_calc", [
        ("berapa barang yang aktif?", ["barang","aktif","item"], [], 5000),
        ("total penjualan bulan ini berapa?", ["penjualan","Rp","total"], [], 8000),
        ("vendor mana yang hutangnya terbesar?", ["vendor","Rp","terbesar","hutang"], [], 15000),
    ]),
]

async def run_test():
    async with httpx.AsyncClient(timeout=60) as client:
        login = await client.post(f"{BASE}/api/auth/login", json={"email":EMAIL,"password":PASSWORD})
        token = login.json()["data"]["access_token"]
        headers = {"Authorization":f"Bearer {token}","Content-Type":"application/json"}
        results = []
        total_pass = total_fail = total_warn = 0

        for group_name, turns in GROUPS:
            conv_id = str(uuid.uuid4())
            print(f"\n{'='*70}\nGROUP: {group_name} (conv={conv_id[:8]})\n{'='*70}")
            for ti, (query, must_contain, must_not_contain, max_lat) in enumerate(turns):
                start = time.time()
                try:
                    resp = await client.post(f"{BASE}/api/v3/chat/message",
                        json={"conversation_id":conv_id,"session_id":conv_id,"text":query}, headers=headers)
                    elapsed = int((time.time()-start)*1000)
                    data = resp.json()
                    text = data.get("text",data.get("response",""))
                    if not text and "data" in data: text = str(data["data"])
                    tl = text.lower()
                    c_pass = not must_contain or any(k.lower() in tl for k in must_contain)
                    nc_pass = True; bad=""
                    for k in must_not_contain:
                        if k.lower() in tl: nc_pass=False; bad=k; break
                    lat_pass = elapsed <= max_lat
                    if c_pass and nc_pass and lat_pass:
                        status="PASS"; total_pass+=1
                    elif not nc_pass:
                        status=f"FAIL(has '{bad}')"; total_fail+=1
                    elif not c_pass:
                        status="WARN(missing kw)"; total_warn+=1
                    else:
                        status=f"SLOW({elapsed}ms>{max_lat}ms)"; total_warn+=1
                    icon="🟢" if elapsed<3000 else "🟡" if elapsed<8000 else "🔴"
                    print(f"  T{ti+1} [{status:25s}] {icon} {elapsed:5d}ms Q: {query}")
                    if not(c_pass and nc_pass):
                        print(f"      → {text[:150].replace(chr(10),' ')}")
                    results.append({"group":group_name,"turn":ti+1,"query":query,
                        "status":status,"latency_ms":elapsed,"response_preview":text[:200]})
                except Exception as e:
                    elapsed=int((time.time()-start)*1000)
                    print(f"  T{ti+1} [ERROR                   ] 🔴 {elapsed:5d}ms Q: {query}")
                    print(f"      → {str(e)[:100]}")
                    total_fail+=1
                    results.append({"group":group_name,"turn":ti+1,"query":query,
                        "status":f"ERROR:{str(e)[:80]}","latency_ms":elapsed,"response_preview":""})
                await asyncio.sleep(1.5)
            await asyncio.sleep(2)

        total=len(results)
        lats=[r["latency_ms"] for r in results if "ERROR" not in r["status"]]
        print(f"\n{'='*70}\nPHASE 2 E2E RESULTS\n{'='*70}")
        print(f"Total: {total} | PASS: {total_pass} ({total_pass/total*100:.0f}%) | FAIL: {total_fail} | WARN: {total_warn}")
        if lats:
            print(f"Latency avg: {sum(lats)/len(lats):.0f}ms | <5s: {sum(1 for l in lats if l<5000)} | 5-15s: {sum(1 for l in lats if 5000<=l<15000)} | >15s: {sum(1 for l in lats if l>=15000)}")
        print(f"\nPer-Group:")
        for gn,_ in GROUPS:
            g=[r for r in results if r["group"]==gn]
            gp=sum(1 for r in g if r["status"]=="PASS")
            ga=sum(r["latency_ms"] for r in g)/max(len(g),1)
            ic="✅" if gp==len(g) else "⚠️" if gp>=len(g)*0.6 else "❌"
            print(f"  {ic} {gn}: {gp}/{len(g)} pass, avg {ga:.0f}ms")
        fails=[r for r in results if r["status"]!="PASS"]
        if fails:
            print(f"\nNon-PASS Details:")
            for r in fails:
                print(f"  [{r['group']}] T{r['turn']} {r['status']}")
                print(f"    Q: {r['query']}")
                print(f"    A: {r['response_preview'][:120]}")
        with open("/root/milkyhoop-dev/backend/docs/reports/phase2-e2e-results.json","w") as f:
            json.dump({"summary":{"pass":total_pass,"fail":total_fail,"warn":total_warn,"total":total},"results":results},f,indent=2)
        print(f"\nSaved to docs/reports/phase2-e2e-results.json")

asyncio.run(run_test())
