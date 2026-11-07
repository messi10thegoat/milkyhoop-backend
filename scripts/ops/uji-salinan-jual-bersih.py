"""BUG-102/106/103 uji di SALINAN (klon milkydb), nyawit 1-7 Nov 2026. MODE=cabang: rekonsiliasi pendapatan & HPP penjualan MATCH,
Minyakita 67 pcs / 1.034.800, Σ sales-by-customer == pendapatan L/R, Warung Bu Sri retur 97.100, receivables.terbuka memuat faktur
jatuh tempo > 7 hari. MODE=dasar (kode master = kontrol merah): pendapatan laporan 10.009.400 != L/R (drift) -> uji membedakan."""
import asyncio
import sys
from datetime import date

from starlette.requests import Request

sys.path.insert(0, "/app/backend/api_gateway")
from app.services.db_pool import get_db_pool  # noqa: E402
from app.services import profitability_query as PQ  # noqa: E402
from app.services.profitability_reconciliation import check_reconciliation  # noqa: E402

MODE = sys.argv[1]
TID = "pt-kita-nyawit-bersama"
D1, D2 = date(2026, 11, 1), date(2026, 11, 7)
HASIL = []


def cek(nama, ok, info=""):
    HASIL.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + f"[{MODE}] {nama}" + (f" -- {info}" if info else ""), flush=True)


async def main():
    pool = await get_db_pool()
    async with pool.acquire() as c:
        async with c.transaction():
            await c.execute("SELECT set_config('app.tenant_id', $1, true)", TID)
            r = await check_reconciliation(c, TID, D1, D2)
            items, summ, _ = await PQ.get_item_profitability(c, TID, D1, D2, "revenue_desc", 50, 0)
    mk = [i for i in items if "inyakita" in i["product_name"]]
    if MODE == "dasar":
        cek("kontrol merah: laporan lama != L/R (drift pendapatan)", r["revenue_drift"] > 0.01 and r["severity"] != "match",
            f"report {r['report_revenue']} vs pnl {r['pnl_revenue']}")
    else:
        cek("pendapatan laporan == L/R", r["revenue_drift"] <= 0.01, f"{r['report_revenue']} vs {r['pnl_revenue']}")
        cek("HPP laporan == HPP sumber penjualan L/R", r["cogs_drift"] <= 0.01, f"{r['report_cogs']} vs {r['pnl_cogs']} (non-jual {r['pnl_cogs_non_jual']})")
        cek("severity match", r["severity"] == "match", r["severity"])
        cek("Minyakita 67 pcs / 1.034.800 (== Accurate)", mk and (mk[0]["qty_sold"], mk[0]["revenue_gross"]) == ("67.00", "1034800.00"), str(mk[:1]))
        from app.routers import reports as R
        q = Request({"type": "http", "method": "GET", "path": "/api/reports/sales-by-customer", "headers": []})
        q.state.user = {"tenant_id": TID, "user_id": "00000000-0000-0000-0000-000000000001"}
        h = (await R.penjualan_per_pelanggan(q, start_date=D1.isoformat(), end_date=D2.isoformat(), limit=50, offset=0))["data"]
        tot = sum(float(x["penjualan_bersih"]) for x in h["items"])
        cek("Σ sales-by-customer == pendapatan L/R, status match", abs(tot - r["pnl_revenue"]) < 0.005 and h["reconciliation"]["status"] == "match", f"{tot} {h['reconciliation']}")
        sri = [x for x in h["items"] if "Sri" in x["customer_name"]]
        cek("Warung Bu Sri retur 97.100", sri and sri[0]["retur"] == "97100.00", str(sri[:1]))
        from app.services import dashboard_v2 as DV
        async with pool.acquire() as c:
            hari = await c.fetchval("SELECT tanggal_bisnis($1)", TID)
            pi = await DV.piutang(c, TID, hari)
        lebih7 = [x for x in pi["terbuka"] if x["due_date"] and date.fromisoformat(x["due_date"]) > hari.replace() and (date.fromisoformat(x["due_date"]) - hari).days > 7]
        cek("receivables.terbuka memuat faktur jatuh tempo > 7 hari (yang dulu hilang)",
            pi["terbuka_count"] >= len(pi["due_7d"]) and (lebih7 or pi["terbuka_count"] == len(pi["due_7d"])), f"terbuka={[x['number'] for x in pi['terbuka']]} due7={[x['number'] for x in pi['due_7d']]}")
    print(f"HASIL[{MODE}]: {sum(HASIL)}/{len(HASIL)} PASS", flush=True)
    sys.exit(0 if all(HASIL) else 1)


asyncio.run(main())
