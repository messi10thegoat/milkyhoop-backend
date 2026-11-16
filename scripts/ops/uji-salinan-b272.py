"""BUG-272(A) uji DB di SALINAN (DRAF, belum pernah dijalankan; perbaiki di slot): services/laporan_nilai_persediaan.muat_nilai_persediaan ATAS klon, tenant kaos + nyawit. Baca-saja.
A paritas dgn penjaga produksi V181: sub_ledger == wac_value dan gl == gl_value dari verify_inventory_wac_reconciliation_all() (per tenant, per hari ini); selisih == drift; status cocok <=> verdict PASS
B per_gudang: Σ nilai baris kelompok=gudang == Σ nilai kelompok=barang (sertakan_nol sama), dan qty barang == Σ qty gudangnya
C Σ nilai baris kelompok=barang (tanpa saringan) == sub_ledger (toleransi pembulatan 0,01 x jumlah baris)
D per tanggal mundur 30 hari: sub_ledger diukur ulang SQL independen (Σ qty x HPP gerakan terakhir sampai tanggal itu)
E waktu (p50/p95, 10 panggilan) per tenant (target < 1000 ms)
F penjualan per barang (profitability/items): teks aditif ada per baris + ringkasan, medan lama sama dgn sebelum teks (string 2dp), kurung bila gross_profit < 0
G kartu stok K9 (handler asli GET /items/{id}/history) pada barang dgn gerakan terbanyak: teks per baris + saldo_awal_text, saldo berjalan akhir == Σ(in-out) SQL, medan lama utuh
Selisih GL nyata (BUG-268, kaos) dicetak sebagai CATATAN lengkap (bukan FAIL): ini bahan analisis penyebab."""
import asyncio
import statistics
import sys
import time
from datetime import date, timedelta
from decimal import Decimal as D

sys.path.insert(0, "/app/backend/api_gateway")
from starlette.requests import Request  # noqa: E402
from app.routers import items as RI  # noqa: E402
from app.routers import reports as RP  # noqa: E402
from app.services import profitability_query as PQ  # noqa: E402
from app.services import teks_laporan_persediaan as TLP  # noqa: E402
from app.services import laporan_nilai_persediaan as LN  # noqa: E402
from app.services.role_resolver import AccountRole, AccountRoleUnmappedError, resolve_account_id_by_role  # noqa: E402

NY, KA = "pt-kita-nyawit-bersama", "kaos-biru-konveksi"
HASIL = []


def cek(nama, ok, info=""):
    HASIL.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + nama + (f" -- {info}" if info else ""), flush=True)


async def main():
    pool = await RP.get_pool()
    async with pool.acquire() as c:
        hari = date.today()
        for tid in (KA, NY):
            try:
                akun = await resolve_account_id_by_role(c, tid, AccountRole.INVENTORY_MERCHANDISE)
            except AccountRoleUnmappedError:
                akun = None
            r = await LN.muat_nilai_persediaan(c, tid, hari, 0, akun_persediaan=akun)
            rk = r["rekonsiliasi"]
            v = await c.fetchrow("/* tenant_id */ SELECT tenant_id, gl_value, wac_value, drift, verdict FROM verify_inventory_wac_reconciliation_all() WHERE tenant_id = $1", tid)
            if v is None:
                print(f"CATATAN {tid}: tak ada baris di verify_inventory_wac_reconciliation_all()", flush=True)
            else:
                cek(f"A {tid}: sub_ledger {rk['sub_ledger']} == wac_value {v['wac_value']}, gl {rk['gl']} == gl_value {v['gl_value']}",
                    abs(D(str(rk["sub_ledger"])) - D(str(v["wac_value"]))) <= D("0.01") and abs(D(str(rk["gl"] or 0)) - D(str(v["gl_value"]))) <= D("0.01"))
                cek(f"A {tid}: selisih {rk['selisih']} == -drift {v['drift']}; status {rk['status']} vs verdict {v['verdict']}",
                    abs(D(str(rk["selisih"] or 0)) + D(str(v["drift"]))) <= D("0.02") and ((rk["status"] == "cocok") == (v["verdict"] in ("PASS", "PASS_EXEMPT"))))
            print(f"CATATAN {tid}: rekonsiliasi {rk['status']} -- {rk['status_text']} (sub {rk['sub_ledger']}, GL {rk['gl']}, toleransi {rk['toleransi']}); {r['jumlah_baris']} barang bernilai, {r['jumlah_minus']} minus", flush=True)
            g = await LN.muat_nilai_persediaan(c, tid, hari, 0, "gudang", akun_persediaan=akun)
            cek(f"B {tid}: Σ nilai per-gudang {g['total_nilai']} == Σ nilai per-barang {r['total_nilai']} (toleransi 0,01 x baris)", abs(D(str(g["total_nilai"])) - D(str(r["total_nilai"]))) <= D("0.01") * (g["jumlah_baris"] + 1))
            qg = {}
            for x in g["baris"]:
                qg[x["item_id"]] = qg.get(x["item_id"], D(0)) + D(str(x["qty"]))
            cek(f"B {tid}: qty tiap barang == Σ qty gudangnya", all(abs(D(str(x["qty"])) - qg.get(x["item_id"], D(0))) < D("0.0001") for x in r["baris"]))
            cek(f"C {tid}: Σ nilai baris {r['total_nilai']} == sub_ledger {rk['sub_ledger']}", abs(D(str(r["total_nilai"])) - D(str(rk["sub_ledger"]))) <= D("0.01") * (r["jumlah_baris"] + 1))
            lalu = hari - timedelta(days=30)
            sql_l = await c.fetchval(
                "/* tenant_id */ WITH w AS (SELECT DISTINCT ON (il.product_id) il.product_id, il.average_cost FROM inventory_ledger il WHERE il.tenant_id = $1 AND il.movement_date <= $2 ORDER BY il.product_id, il.created_at DESC, il.id DESC), "
                "q AS (SELECT il.product_id, SUM(COALESCE(il.quantity_in, 0) - COALESCE(il.quantity_out, 0)) AS qty FROM inventory_ledger il WHERE il.tenant_id = $1 AND il.movement_date <= $2 GROUP BY il.product_id) "
                "SELECT COALESCE(SUM(q.qty * w.average_cost), 0) FROM q JOIN w ON w.product_id = q.product_id", tid, lalu)
            rl = (await LN.muat_nilai_persediaan(c, tid, lalu, 0, akun_persediaan=akun))["rekonsiliasi"]
            cek(f"D {tid}: per {lalu}: sub_ledger {rl['sub_ledger']} == SQL independen {sql_l:.2f}", abs(D(str(rl["sub_ledger"])) - D(str(sql_l))) <= D("0.01"))
            t = []
            for _ in range(10):
                a0 = time.perf_counter()
                await LN.muat_nilai_persediaan(c, tid, hari, 0, akun_persediaan=akun)
                t.append(time.perf_counter() - a0)
            t.sort()
            cek(f"E {tid}: p50 {statistics.median(t) * 1000:.0f} ms, p95 {t[8] * 1000:.0f} ms (target < 1000 ms)", t[8] < 1.0)
            # F penjualan per barang + teks
            sd, ed = date(hari.year, 1, 1), hari
            items, summ, n = await PQ.get_item_profitability(c, tid, sd, ed, "revenue_desc", 50, 0)
            data = {"period": {"start_date": sd.isoformat(), "end_date": ed.isoformat(), "days": (ed - sd).days + 1}, "summary": dict(summ), "items": [dict(i) for i in items],
                    "pagination": {"total": n}, "reconciliation": None}
            lama = [dict(i) for i in items]
            TLP.tambah_teks_penjualan_barang(data, 0)
            ok = all(("revenue_gross_text" in i and "qty_sold_text" in i and "margin_text" in i) for i in data["items"]) and all(all(i[k] == v for k, v in l.items()) for i, l in zip(data["items"], lama))
            ok = ok and all((i["gross_profit_text"].startswith("(")) == (D(i["gross_profit"]) < 0) for i in data["items"]) and "total_gross_profit_text" in data["summary"] and bool(data["periode_text"])
            cek(f"F {tid}: penjualan per barang {len(items)} baris: teks lengkap, medan lama utuh, kurung <=> gross_profit < 0", ok)
            # G kartu stok K9
            top = await c.fetchrow("/* tenant_id */ SELECT product_id, count(*) AS n, COALESCE(SUM(COALESCE(quantity_in, 0) - COALESCE(quantity_out, 0)), 0) AS saldo FROM inventory_ledger WHERE tenant_id = $1 GROUP BY product_id ORDER BY n DESC LIMIT 1", tid)
            if top is None:
                print(f"CATATAN {tid}: tak ada gerakan ledger, G dilewati", flush=True)
            else:
                uid = await c.fetchval("/* tenant_id */ SELECT utr.user_id FROM user_tenant_roles utr JOIN roles r ON r.id = utr.role_id WHERE utr.tenant_id = $1 AND utr.status = 'ACTIVE' AND r.name ILIKE '%owner%' LIMIT 1", tid)
                rq = Request({"type": "http", "method": "GET", "path": "/x", "headers": []})
                rq.state.user = {"tenant_id": tid, "user_id": str(uid), "username": "uji-salinan", "role": "OWNER"}
                h = await RI.get_item_history(rq, top["product_id"], page=1, limit=200, warehouse_id=None, date_from=None, date_to=None)
                satu = h["data"]
                ok = all(k in satu[0] for k in ("tanggal_text", "arah", "quantity_balance_text", "unit_cost_text", "perubahan_text")) and "saldo_awal_text" in h and h["item"].get("satuan") is not None
                akhir = max(satu, key=lambda m: (m["movement_date"], m["created_at"])) if satu else None
                cek(f"G {tid}: kartu stok {h['total']} gerakan: teks per baris + satuan, medan lama utuh", ok and "movement_type" in satu[0])
                if h["total"] <= 200 and akhir is not None:
                    cek(f"G {tid}: saldo berjalan terakhir {akhir['quantity_balance']} == Σ(in-out) SQL {top['saldo']}", abs(D(str(akhir["quantity_balance"])) - D(str(top["saldo"]))) < D("0.0001"))
    print(f"HASIL: {sum(HASIL)}/{len(HASIL)} PASS", flush=True)
    sys.exit(0 if all(HASIL) else 1)


asyncio.run(main())
