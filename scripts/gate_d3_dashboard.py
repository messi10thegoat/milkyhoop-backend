"""Gerbang D3 (06-GERBANG-UJI §A): angka dashboard v2 == laporan, per tenant & periode. READ-ONLY.

Dijalankan di kontainer SEKALI-PAKAI (image gateway, worktree :ro) — bukan docker exec ke gateway prod:
  scripts/gate_d3_dashboard.sh [tenant ...]
Pembanding = fungsi LAPORAN yang dipakai layar laporan:
  Laba Rugi / Neraca / Arus Kas = services/report_engine (GET /api/reports/psak/financial-statements)
  Umur Piutang / Hutang         = routers/reports.get_aging_receivable / get_aging_payable (dipanggil langsung)
Selisih wajib 0. `--kontrol-merah`: geser satu angka dashboard Rp 1 -> gerbang WAJIB gagal (Law 33).
Juga: validasi kontrak JSON pada data NYATA + p95 (20 putaran) ringkasan & tugas.
"""
import asyncio
import json
import os
import sys
import time
from datetime import date, datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

sys.path.insert(0, "/app/backend/api_gateway")
sys.path.insert(0, "/app/backend/api_gateway/tests/unit")
os.environ.setdefault("OPENAI_API_KEY", "sk-boneka-gerbang")

import asyncpg  # noqa: E402

from app.services import dashboard_v2 as DV  # noqa: E402
from app.services.report_engine.balance_sheet import generate_balance_sheet  # noqa: E402
from app.services.report_engine.cash_flow import generate_cash_flow  # noqa: E402
from app.services.report_engine.income_statement import generate_income_statement  # noqa: E402
from app.routers import reports as RPT  # noqa: E402
from app.utils.tanggal_tenant import tanggal_dokumen, zona_tenant  # noqa: E402
from tests.unit.kontrak_json import _cocok, _skema  # noqa: E402

KONTROL_MERAH = "--kontrol-merah" in sys.argv
TENANT = [a for a in sys.argv[1:] if not a.startswith("--")] or ["grapgrap-manado"]
D = lambda v: Decimal(str(v)).quantize(Decimal("0.01"))  # noqa: E731


class _Pool:
    def __init__(self, conn):
        self.conn = conn

    def acquire(self):
        c = self.conn

        class _Ctx:
            async def __aenter__(s):
                return c

            async def __aexit__(s, *a):
                return False
        return _Ctx()


async def main():
    conn = await asyncpg.connect(os.environ["DATABASE_URL"])
    await conn.execute("SET default_transaction_read_only = on")  # gerbang tak mungkin menulis
    gagal, baris = 0, []

    async def _pool():
        return _Pool(conn)
    RPT.get_pool = _pool

    def banding(tenant, per, nama, dash, lap):
        nonlocal gagal
        dash, lap = D(dash), D(lap)
        if KONTROL_MERAH and nama == "Laba":
            dash += 1
        ok = dash == lap
        gagal += 0 if ok else 1
        baris.append(f"| {tenant} | {per} | {nama} | {dash:,} | {lap:,} | {dash - lap:,} | {'OK' if ok else 'SELISIH'} |")

    for t in TENANT:
        zona = await zona_tenant(conn, t)
        hari_ini = await tanggal_dokumen(conn, t)
        req = SimpleNamespace(state=SimpleNamespace(user={"tenant_id": t, "user_id": "gerbang-d3"}))
        umur_ar = await RPT.get_aging_receivable(req)
        umur_ap = await RPT.get_aging_payable(req)
        nrc = await generate_balance_sheet(conn, t, hari_ini.isoformat())
        kas_nrc = nrc["aset_lancar"]["kas_setara_kas"]
        for per in DV.PERIODE:
            s = await DV.ringkasan(conn, t, per, hari_ini, zona.key, datetime.now(timezone.utc).isoformat())
            mulai, akhir = DV.rentang_periode(per, hari_ini)
            lr = await generate_income_statement(conn, t, mulai.isoformat(), akhir.isoformat())
            pend = lr["pendapatan"]["total"] + lr["pendapatan_lain"]["total"]
            beban = lr["hpp"]["total"] + lr["beban_usaha"]["total"] + lr["beban_lain"]["total"]
            banding(t, per, "Pendapatan", s["pnl"]["revenue"], pend)
            banding(t, per, "Beban", s["pnl"]["expense"], beban)
            banding(t, per, "Laba", s["pnl"]["profit"], lr["laba_bersih"])
            banding(t, per, "Σ kategori Ke mana uang pergi", sum(D(c["amount"]) for c in s["expenses"]["categories"]), beban)
            past = [b for b in s["cashflow"]["buckets"] if not b["is_projection"]]
            cf = await generate_cash_flow(conn, t, past[0]["start"], past[-1]["end"])
            banding(t, per, "Arus kas masuk−keluar", D(s["cashflow"]["total_in"]) - D(s["cashflow"]["total_out"]),
                    cf["kenaikan_kas_bersih"])
            if per == "month":
                banding(t, "per hari ini", "Uang tersedia", s["cash"]["total"], kas_nrc["total"])
                per_akun = {a["account_name"]: a["balance"] for a in kas_nrc["akun"]}
                for a in s["cash"]["accounts"]:
                    banding(t, "per hari ini", f"· {a['name']} ({a['type']})", a["balance"], per_akun.get(a["name"], 0))
                banding(t, "per hari ini", "Piutang total", s["receivables"]["total"], umur_ar["total_outstanding"])
                banding(t, "per hari ini", "Piutang belum jatuh tempo", s["receivables"]["not_due"], umur_ar["summary"]["current"])
                banding(t, "per hari ini", "Piutang telat",
                        s["receivables"]["overdue_total"], sum(v for k, v in umur_ar["summary"].items() if k != "current"))
                banding(t, "per hari ini", "Hutang total", s["payables"]["total"], umur_ap["total_outstanding"])
                sk = _skema("dashboard-summary.schema.json")
                g = _cocok(json.loads(json.dumps(s)), sk, sk)
                baris.append(f"| {t} | kontrak summary | {'VALID' if not g else g[:3]} |")
                gagal += 1 if g else 0
                print(f"[{t}] kas={s['cash']['cash_total']} bank={s['cash']['bank_total']} "
                      f"periode Laba {mulai}..{akhir}")
        tugas = await DV.tugas_tenant(conn, t, hari_ini)
        badan = json.loads(json.dumps(DV.rangkum_tugas(tugas, set(), 0, datetime.now(timezone.utc).isoformat())))
        sk = _skema("dashboard-tasks.schema.json")
        g = _cocok(badan, sk, sk)
        gagal += 1 if g else 0
        jenis = {}
        for x in badan["tasks"]:
            jenis[x["type"]] = jenis.get(x["type"], 0) + 1
        baris.append(f"| {t} | kontrak tasks | {'VALID' if not g else g[:3]} | tugas={jenis} sum_now={badan['sum_now']} |")

        # kinerja: 20 putaran tanpa cache
        for nama, fn in (("summary(month)", lambda: DV.ringkasan(conn, t, "month", hari_ini, zona.key, "x")),
                         ("summary(year)", lambda: DV.ringkasan(conn, t, "year", hari_ini, zona.key, "x")),
                         ("tasks", lambda: DV.tugas_tenant(conn, t, hari_ini))):
            w = []
            for _ in range(20):
                a = time.perf_counter()
                await fn()
                w.append((time.perf_counter() - a) * 1000)
            w.sort()
            baris.append(f"| {t} | p95 {nama} | {w[18]:.0f} ms (median {w[10]:.0f}) |")

    print("| tenant | periode | angka | dashboard | laporan | selisih | |")
    print("|---|---|---|---|---|---|---|")
    print("\n".join(baris))
    await conn.close()
    print(f"GAGAL={gagal}")
    sys.exit(1 if gagal else 0)


asyncio.run(main())
