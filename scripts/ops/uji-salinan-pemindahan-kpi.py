"""KPI Pemindahan ikut Diterima uji DB di SALINAN (bukan prod): handler ASLI GET /stock-transfers/summary, tenant kaos + nyawit. Hanya BACA.
A handler.selesai_bulan_ini == SQL independen (status IN completed,received, received_date dalam bulan bisnis); nilai sama
B sebelum (hanya completed) + tambahan Diterima = sesudah; in_transit/draft/voided tak ikut
C kaos bulan 2026-11: sesudah 278.850, sebelum 267.750 (tambahan Diterima 11.100)"""
import asyncio
import sys
from datetime import date
from decimal import Decimal

from starlette.requests import Request

sys.path.insert(0, "/app/backend/api_gateway")
from app.routers import items as IT  # noqa: E402
from app.routers import stock_transfers as ST  # noqa: E402

TENANT = ("pt-kita-nyawit-bersama", "kaos-biru-konveksi")
HASIL = []


def cek(nama, ok, info=""):
    HASIL.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + nama + (f" -- {info}" if info else ""), flush=True)


def req(tid):
    r = Request({"type": "http", "method": "GET", "path": "/x", "headers": []})
    r.state.user = {"tenant_id": tid, "user_id": "uji-salinan", "username": "uji-salinan", "role": "OWNER"}
    return r


async def main():
    conn = await IT.get_db_connection()
    for tid in TENANT:
        d = (await ST.summary_stock_transfers(req(tid)))["data"]
        awal = date.fromisoformat(d["bulan"] + "-01")
        akhir = date(awal.year + (awal.month == 12), awal.month % 12 + 1, 1)

        async def agg(statuses):
            r = await conn.fetchrow(
                "SELECT count(*) AS n, COALESCE(SUM(total_value), 0) AS v FROM stock_transfers WHERE tenant_id = $1 "
                "AND status = ANY($2::text[]) AND received_date >= $3 AND received_date < $4", tid, statuses, awal, akhir)
            return r["n"], Decimal(r["v"])
        n_sesudah, v_sesudah = await agg(["completed", "received"])
        n_sebelum, v_sebelum = await agg(["completed"])
        n_tambah, v_tambah = await agg(["received"])
        cek(f"A {tid}: selesai_bulan_ini == SQL independen", d["selesai_bulan_ini"] == n_sesudah, f"{d['selesai_bulan_ini']} vs {n_sesudah}")
        cek(f"A {tid}: nilai == SQL independen", Decimal(str(d["nilai_selesai_bulan_ini"])) == v_sesudah, f"{d['nilai_selesai_bulan_ini']} vs {v_sesudah}")
        cek(f"B {tid}: sebelum + Diterima = sesudah", n_sebelum + n_tambah == n_sesudah and v_sebelum + v_tambah == v_sesudah,
            f"sebelum {v_sebelum} + Diterima {v_tambah} = {v_sesudah}")
        print(f"   {tid} bulan {d['bulan']}: sebelum={v_sebelum} ({n_sebelum}), Diterima={v_tambah} ({n_tambah}), sesudah={v_sesudah} ({n_sesudah})")
        if tid == "kaos-biru-konveksi" and d["bulan"] == "2026-11":
            cek("C kaos sesudah 278.850", v_sesudah == Decimal("278850"), str(v_sesudah))
            cek("C kaos sebelum 267.750 (3 Selesai)", v_sebelum == Decimal("267750") and n_sebelum == 3, f"{v_sebelum} ({n_sebelum})")
            cek("C kaos Diterima 11.100", v_tambah == Decimal("11100"), str(v_tambah))
    ok = all(HASIL) and len(HASIL) > 0
    print(f"{sum(HASIL)}/{len(HASIL)} lulus")
    sys.exit(0 if ok else 1)

asyncio.run(main())
